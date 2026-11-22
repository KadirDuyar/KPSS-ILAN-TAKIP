#!/usr/bin/env python3
"""Profil ile ilanları eşleştirir; istenirse Telegram'a gönderir.

  python kk_filter.py --db deneme.db                  # varsayılan profil, ekrana yaz
  python kk_filter.py --db deneme.db --profile varsayilan --verbose   # elenenleri de göster
  python kk_filter.py --db deneme.db --telegram --dry-run             # göndermeden dene
  TELEGRAM_BOT_TOKEN=... python kk_filter.py --db deneme.db --telegram

Karar düzeyleri: UYGUN (ok) / KONTROL ET (check: bilgi eksik/okunamadı) / UYGUN DEĞİL (no).
Okunamayan şart asla "uygun değil" sayılmaz; "kontrol et" olur.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime

from . import db as dbm
from . import requirements as reqs

DETAIL_URL = "https://kariyerkapisi.gov.tr/IlanDetay?i={guid}"
LEVEL = {"ok": 0, "note": 0, "check": 1, "no": 2}
LABEL = {"ok": "✅ UYGUN", "check": "⚠️ KONTROL ET", "no": "❌ UYGUN DEĞİL"}
EDU = {"LİSE": 1, "ÖNLİSANS": 2, "LİSANS": 3, "YÜKSEKLİSANS": 4}
_UP = str.maketrans({"i": "İ", "ı": "I"})


def tr_upper(s) -> str:
    return (s or "").translate(_UP).upper()


def edu_rank(text):
    t = tr_upper(text).replace(" ", "")
    for key in ("YÜKSEKLİSANS", "ÖNLİSANS", "LİSANS", "LİSE"):  # sıra önemli
        if key in t:
            return EDU[key]
    return None


def years_between(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


# ---------- Kadro kontrolü ----------
def pref(prof: dict, key: str, default=None):
    return (prof.get("preferences") or {}).get(key, default)


def _merge_structured(reqs_list: list, p: dict) -> list:
    """Kadro alanlarında yapısal veri var, metinde yoksa ekle (metin önceliklidir)."""
    kinds = {r["kind"] for r in reqs_list}
    if "kpss_type" not in kinds and p.get("kpss_score_types"):
        reqs_list = [r for r in reqs_list if r["kind"] != "kpss_unknown"]
        reqs_list.append({"kind": "kpss_type", "value": p["kpss_score_types"], "evidence": "kadro alanı"})
    if "kpss_min_score" not in kinds and p.get("min_score") is not None:
        reqs_list.append({"kind": "kpss_min_score", "value": p["min_score"], "evidence": "kadro alanı"})
    if "age_max" not in kinds and p.get("max_age") is not None:
        reqs_list.append({"kind": "age_max", "value": p["max_age"], "evidence": "kadro alanı"})
    if "education_level" not in kinds and reqs.edu_rank(p.get("education")) is not None:
        reqs_list.append({"kind": "education_level", "value": reqs.edu_rank(p.get("education")),
                          "evidence": p.get("education") or ""})
    if "gender" not in kinds:
        g = tr_upper(p.get("gender"))
        only = "E" if "ERKEK" in g and "KADIN" not in g else "K" if "KADIN" in g and "ERKEK" not in g else None
        if only:
            reqs_list.append({"kind": "gender", "value": only, "evidence": p.get("gender")})
    return reqs_list


def check_position(p: dict, deadline: datetime, prof: dict, ilan: dict = None, ilan_type: str = None):
    """[(düzey, gerekçe)] — düzey: ok/check/no/note. Sadece ok olmayanlar döner."""
    res = []
    ilan = ilan or {}
    wanted = {tr_upper(x) for x in pref(prof, "provinces", []) or []}
    if wanted:
        text = " ".join(tr_upper(x["il"]) for x in p.get("provinces", []))
        toks = set(re.split(r"[^A-ZÇĞİÖŞÜ]+", text))
        if not p.get("provinces"):
            res.append(("check", "il bilgisi yok"))
        elif not ("TÜRKİYE GENELİ" in text or wanted & toks):
            res.append(("no", "il uyuşmuyor"))
    rl = _merge_structured(reqs.extract_position(p, ilan, ilan_type), p)
    for r in reqs.evaluate_all(rl, prof, deadline):
        if r["level"] != "ok":
            res.append((r["level"], r["msg"]))
    return res


def keyword_ok(p, title, prof) -> bool:
    hay = tr_upper(f"{p.get('unvan') or ''} {title or ''}")
    unvan = tr_upper(p.get("unvan"))
    if any(tr_upper(k) in unvan for k in pref(prof, "keywords_exclude", []) or []):
        return False
    inc = pref(prof, "keywords_include", []) or []
    return not inc or any(tr_upper(k) in hay for k in inc)


def evaluate(row, prof, now: datetime):
    """Dönüş: None (ilgisiz/süresi geçmiş) veya {level, rows, ...}."""
    types = pref(prof, "ilan_types", []) or []
    if types and row["ilan_type"] not in types:
        return None
    dl = row["application_deadline"]
    deadline = datetime.fromisoformat(dl) if dl else datetime(2999, 1, 1)  # süre yoksa açık uçlu
    if deadline < now:
        return None
    d = json.loads(row["detail_json"]) if row["detail_json"] else None
    positions = (d or {}).get("positions") or []
    ilan = d or {}

    cands = []
    if not positions:
        inc = pref(prof, "keywords_include", []) or []
        if not inc or any(tr_upper(k) in tr_upper(row["title"]) for k in inc):
            cands.append(("check", None, ["kadro ayrıntısı alınamadı"]))
    for p in positions:
        if not keyword_ok(p, row["title"], prof):
            continue
        res = check_position(p, deadline, prof, ilan, row["ilan_type"])
        lvl = max((LEVEL[x[0]] for x in res), default=0)
        level = [k for k, v in LEVEL.items() if v == lvl][0]
        cands.append((level, p, [r for _, r in res]))
    if not cands:
        return None
    best = min(LEVEL[c[0]] for c in cands)
    return {"level": [k for k, v in LEVEL.items() if v == best][0], "cands": cands,
            "deadline": deadline, "started": not row["application_start"] or datetime.fromisoformat(row["application_start"]) <= now}


# ---------- Biçimlendirme ----------
def format_message(row, ev, now, verbose=False) -> str:
    open_end = ev["deadline"].year >= 2999
    when = "süre belirtilmemiş" if open_end else f"{ev['deadline']:%d.%m.%Y %H:%M} ({(ev['deadline'].date() - now.date()).days} gün)"
    lines = [f"{LABEL[ev['level']]}  |  son: {when}",
             f"{row['institution']}", row["title"] if len(row["title"]) < 140 else row["title"][:137] + "..."]
    if not ev["started"]:
        lines.append(f"⏳ başvuru {datetime.fromisoformat(row['application_start']):%d.%m.%Y %H:%M}'de başlıyor")
    shown = [c for c in ev["cands"] if verbose or c[0] != "no"]
    for level, p, reasons in shown[:5]:
        if p:
            il = ", ".join(f"{x['il']} ({x['kontenjan']})" for x in p["provinces"][:2])
            bits = [p["unvan"], "/".join(p.get("kpss_score_types") or []) or None,
                    f">={p['min_score']:g}" if p.get("min_score") else None, il or None]
            lines.append("• " + " | ".join(b for b in bits if b))
        for r in reasons:
            lines.append(f"    ↳ {r}")
    if len(shown) > 5:
        lines.append(f"  (+{len(shown) - 5} kadro daha)")
    lines.append(row["url"] or DETAIL_URL.format(guid=row["guid"]))
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    import requests

    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                      timeout=20)
    r.raise_for_status()


# ---------- Ana akış ----------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="SQLite yolu veya postgresql:// adresi")
    ap.add_argument("--profiles", default="profiles.json")
    ap.add_argument("--profile", help="sadece bu profil")
    ap.add_argument("--today", help="YYYY-MM-DD (test için)")
    ap.add_argument("--verbose", action="store_true", help="UYGUN DEĞİL kadroları da göster")
    ap.add_argument("--telegram", action="store_true", help="yeni eşleşmeleri Telegram'a gönder")
    ap.add_argument("--dry-run", action="store_true", help="göndermeden yaz, kaydetme")
    a = ap.parse_args(argv)

    if not os.path.exists(a.profiles):
        a.profiles = "profiles.example.json"  # kişisel profil yoksa örnek profil
    now = datetime.fromisoformat(a.today + "T00:00:00") if a.today else datetime.now()
    with open(a.profiles, encoding="utf-8") as f:
        profiles = [reqs.normalize_profile(x) for x in json.load(f)["profiles"]]
    if a.profile:
        profiles = [p for p in profiles if p["name"] == a.profile]

    con = dbm.connect(a.db)
    con.execute("""CREATE TABLE IF NOT EXISTS notified (
        profile TEXT, guid TEXT, fp TEXT, ts TEXT, PRIMARY KEY (profile, guid, fp))""")
    if not con.table_exists("ilan"):
        print("DB'de ilan tablosu yok; önce kk_tracker.py --db <dosya> çalıştır.")
        return 1
    con.ensure_schema()
    rows = con.execute("SELECT * FROM ilan WHERE active=1 ORDER BY application_deadline").fetchall()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")

    for prof in profiles:
        allowed = {"ok", "check"} if pref(prof, "notify_check", True) else {"ok"}
        hits = []
        for row in rows:
            ev = evaluate(row, prof, now)
            if ev and (ev["level"] in allowed or a.verbose):
                hits.append((row, ev))
        print(f"\n=== {prof['name']}: {len(hits)} eşleşme ===")
        for row, ev in hits:
            msg = format_message(row, ev, now, a.verbose)
            print("\n" + msg)
            if not a.telegram or ev["level"] not in allowed:
                continue
            seen = con.execute("SELECT 1 FROM notified WHERE profile=? AND guid=? AND fp=?",
                               (prof["name"], row["guid"], row["list_fp"])).fetchone()
            if seen:
                continue
            chat = prof.get("telegram_chat_id")
            if a.dry_run or not (token and chat):
                why = "dry-run" if a.dry_run else "token/chat_id yok"
                print(f"  [telegram atlandı: {why}]")
                continue
            try:
                send_telegram(token, chat, msg)
                con.execute("INSERT INTO notified VALUES (?,?,?,?)",
                            (prof["name"], row["guid"], row["list_fp"], now.isoformat()))
                con.commit()
                print("  [telegram gönderildi]")
            except Exception as e:
                print(f"  [telegram hatası: {e}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
