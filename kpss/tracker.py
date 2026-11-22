#!/usr/bin/env python3
"""Kariyer Kapısı liste adapter'ı + SQLite ile yeni/değişen ilan takibi.

kk_test.py (güncel sürüm) aynı klasörde olmalı.

  python kk_tracker.py                      # canlı: listeyi çek, yeni/değişenleri işle
  python kk_tracker.py --reparse            # kayıtlı ham cevapları ağsız yeniden ayrıştır
  python kk_tracker.py --list-file liste.json --no-detail --db test.db   # ağsız test

Her çalışmada 1 liste isteği atar; ayrıntı (2 istek) YENİ veya DEĞİŞEN ilan için, ayrıca listede
değişmeyen ilanların ayrıntısı en geç REFRESH_HOURS saatte bir yenilenir (çalışma başına en çok REFRESH_MAX).
Ham cevaplar DB'de saklanır: ayrıştırıcıyı geliştirince tekrar istek atmadan --reparse yeter.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone

from . import db as dbm
from . import kariyer_kapisi as kk

LIST_API = "https://api.kariyerkapisi.gov.tr/api/ilan/GetIseAlimPage"
LIST_PAYLOAD = {"krM_ID": 0, "searchText": "", "il": "0", "ilanTuru": "0"}
DELAY = 1.5  # ayrıntı istekleri arası bekleme (sn)
# Liste değişmese de ilan metni/kadro değişebilir: her ilanın ayrıntısı en geç REFRESH_HOURS saatte bir
# yeniden çekilir; her çalışmada en çok REFRESH_MAX ilan (en eskiden başlayarak), istek sayısı düşük kalır.
REFRESH_HOURS = 12
REFRESH_MAX = 6

# ---------- HTTP ----------
def make_session():
    import requests

    s = requests.Session()
    s.headers.update(kk.HEADERS)
    return s


def post_json(session, url, payload, retries=3, timeout=20):
    last = None
    for i in range(retries):
        try:
            r = session.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if 400 <= r.status_code < 500 and r.status_code != 429:
                break  # istemci hatası: tekrar denemenin anlamı yok
        except Exception as e:  # ağ/zaman aşımı/JSON
            last = repr(e)
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"{url} başarısız: {last}")


# ---------- Dönüştürme ----------
def list_record(it: dict) -> dict:
    return {
        "guid": it["guid"],
        "institution": it.get("kurumAdi"),
        "unit": (it.get("birimAdi") or "").strip() or None,
        "title": it.get("ilanBaslik"),
        "ilan_type": (it.get("ilanTuru") or "").strip(", ") or None,
        "application_start": it.get("basTarih"),
        "application_deadline": it.get("bitTarih"),
        "external_url": it.get("basvuruLinki") or None,
        "url": kk.DETAIL_URL.format(guid=it["guid"]),  # kullanıcıya verilecek resmî sayfa
    }


def fingerprint(rec: dict) -> str:
    keys = ("title", "application_start", "application_deadline", "external_url")
    return "|".join(str(rec[k]) for k in keys)


def check_total(raw: dict):
    total = sum(k.get("ilanCount", 0) for k in raw.get("ilanCountForKurum", []))
    got = len(raw.get("searchIlan", []))
    if total != got:
        print(f"[uyarı] listede {got} ilan var, kurum sayaçları toplamı {total} "
              "(sayfalama/kesilme olabilir)", file=sys.stderr)


def content_fp(item: dict) -> str:
    """Kullanıcıyı etkileyen içeriğin parmak izi: ilan metni, tarihler, kontenjan ve kadrolar
    (unvan, şart metni, adet, il/kontenjan). Ham alanlardan: ayrıştırıcı sürümünden bağımsız."""
    pos = [[p.get("unvan"), p.get("requirements_text"), p.get("quantity"),
            [[x.get("il"), x.get("kontenjan")] for x in p.get("provinces") or []]]
           for p in item.get("positions") or []]
    base = [item.get("requirements_text"), item.get("application_start"), item.get("application_deadline"),
            item.get("quota"), pos]
    return hashlib.sha256(json.dumps(base, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_item(guid: str, raw_detail: dict, raw_alt) -> dict:
    """Ham cevaplardan ortak modeli üretir (ağ yok; --reparse bunu kullanır)."""
    item = kk.normalize(raw_detail, guid)
    item["kpss_text_types"] = item.get("kpss_score_types")  # sadece ana ilan metninden (kadro birleşiminden önce)
    if raw_alt is None:
        item["needs_review"].append("alt_ilan_failed")
        return item
    try:
        item["positions"] = kk.parse_positions(raw_alt)
    except Exception as e:
        item["needs_review"].append("alt_parse_failed")
        print(f"[alt] {guid}: {e}", file=sys.stderr)
        return item
    types = {t for p in item["positions"] for t in (p.get("kpss_score_types") or [])}
    if types:
        item["kpss_score_types"] = sorted(types, key=lambda x: int(x[1:]))
        item["needs_review"] = [x for x in item["needs_review"] if x != "kpss_not_found_in_text"]
    item["content_hash"] = content_fp(item)
    return item


def fetch_detail(session, guid: str):
    raw_detail = post_json(session, kk.API, {"ilanGuid": guid})
    try:
        raw_alt = post_json(session, kk.ALT_API, {"ilanGuid": guid})
    except Exception as e:
        raw_alt = None
        print(f"[alt] {guid}: {e}", file=sys.stderr)
    refs = [{"kind": "detail", "url": kk.API, "payload": {"ilanGuid": guid}},
            {"kind": "alt", "url": kk.ALT_API, "payload": {"ilanGuid": guid}}]
    item = build_item(guid, raw_detail, raw_alt)
    item["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return item, {"detail": raw_detail, "alt": raw_alt, "refs": refs}


def reparse(con, only_stale=False) -> int:
    """Ham cevaplardan ağsız yeniden ayrıştırır. only_stale: yalnızca eski ayrıştırıcı sürümüyle kaydedilenler."""
    n = 0
    for r in con.execute("SELECT guid, raw_json, detail_json FROM ilan WHERE raw_json IS NOT NULL").fetchall():
        if only_stale:
            try:
                if (json.loads(r["detail_json"] or "{}") or {}).get("parser") == kk.PARSER_VERSION:
                    continue
            except ValueError:
                pass
        b = json.loads(r["raw_json"])
        item = build_item(r["guid"], b["detail"], b.get("alt"))
        try:  # ağsız yeniden ayrıştırma ayrıntının çekildiği zamanı değiştirmez
            item["fetched_at"] = (json.loads(r["detail_json"] or "{}") or {}).get("fetched_at")
        except ValueError:
            pass
        con.execute("UPDATE ilan SET detail_json=?, content_hash=? WHERE guid=?",
                    (json.dumps(item, ensure_ascii=False), item["content_hash"], r["guid"]))
        n += 1
    con.commit()
    return n


def _refresh_key(detail_json, now: datetime):
    """Yenilenmesi gerekiyorsa sıralama anahtarı ("" = hiç zaman bilgisi yok, en önce), gerekmiyorsa None."""
    try:
        ts = (json.loads(detail_json) or {}).get("fetched_at") if detail_json else None
        when = datetime.fromisoformat(ts) if ts else None
    except (ValueError, TypeError):
        return ""
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return ts if now - when >= timedelta(hours=REFRESH_HOURS) else None


def refresh_details(con, session, guids, now_iso: str, limit: int = REFRESH_MAX):
    """Listede değişmeyen ilanların ayrıntısını en eskiden başlayarak yeniden çeker ve kaydeder.
    Dönüş: (metni değişen sayısı, hata sayısı). Metni değişen ilanda (PostgreSQL'de) o ilanla eşleşmesi
    olan kullanıcılara REQUIREMENT_CHANGED olayı yazılır; eşleşmeler zaten bir sonraki sync'te yeniden hesaplanır."""
    now = datetime.fromisoformat(now_iso)
    stale = []
    for guid in guids:
        row = con.execute("SELECT detail_json FROM ilan WHERE guid=?", (guid,)).fetchone()
        if row is None:
            continue
        key = _refresh_key(row["detail_json"], now)
        if key is not None:
            stale.append((key, guid, row["detail_json"]))
    stale.sort()
    changed = errors = 0
    for _, guid, old_json in stale[:limit]:
        try:
            detail, bundle = fetch_detail(session, guid)
        except Exception as e:  # eski kayıt kalır, sonraki çalışmada yeniden denenir
            errors += 1
            print(f"[yenileme] {guid}: {e}", file=sys.stderr)
            continue
        finally:
            time.sleep(DELAY)
        try:
            old = json.loads(old_json) if old_json else None
        except ValueError:
            old = None
        refs = [{"kind": "list", "url": LIST_API, "payload": LIST_PAYLOAD, "fetched_at": now_iso}]
        refs += [{**r, "fetched_at": now_iso} for r in bundle["refs"]]
        con.execute("UPDATE ilan SET detail_json=?, raw_json=?, content_hash=?, source_refs=? WHERE guid=?",
                    (json.dumps(detail, ensure_ascii=False),
                     json.dumps({k: v for k, v in bundle.items() if k != "refs"}, ensure_ascii=False),
                     detail["content_hash"], json.dumps(refs, ensure_ascii=False), guid))
        if old is not None and content_fp(old) != detail["content_hash"]:
            changed += 1
            print(f"METİN DEĞİŞTİ {detail.get('title')}\n         {kk.DETAIL_URL.format(guid=guid)}")
            if con.pg:
                con.execute(
                    """INSERT INTO notification_outbox (user_id, guid, event, payload)
                       SELECT user_id, guid, 'REQUIREMENT_CHANGED', cast(? as jsonb) FROM matches
                       WHERE guid=? AND level IN ('ok','check') ON CONFLICT DO NOTHING""",
                    (json.dumps({"fp": detail["content_hash"], "title": detail.get("title")}, ensure_ascii=False),
                     guid))
    return changed, errors


# ---------- Çıktı ----------
def fmt_dt(s):
    return (s or "?").replace("T", " ")[:16]


def describe(kind, rec, detail):
    print(f"{kind.upper():8}[{rec['ilan_type']}] {rec['title']}")
    print(f"         başvuru: {fmt_dt(rec['application_start'])} → {fmt_dt(rec['application_deadline'])}")
    print(f"         {kk.DETAIL_URL.format(guid=rec['guid'])}")
    if rec["external_url"]:
        print(f"         dış başvuru linki: {rec['external_url']}")
    for p in (detail or {}).get("positions", [])[:3]:
        il = ", ".join(f"{x['il']} ({x['kontenjan']})" for x in p["provinces"][:3])
        bits = [p["unvan"], f"x{p['quantity']}" if p["quantity"] else None,
                "/".join(p.get("kpss_score_types") or []) or None,
                f">={p['min_score']:g}" if p["min_score"] else None,
                f"<={p['max_age']} yaş" if p["max_age"] else None, il or None]
        print("         kadro:", " | ".join(b for b in bits if b))
    if detail and detail["needs_review"]:
        print("         kontrol et:", ", ".join(detail["needs_review"]))


# ---------- Ek kaynaklar (sources/) ----------
def ingest_generic(con, name, records, now) -> int:
    """Kadro ayrıntısı olmayan kaynaklar: başlık/kurum/tarih/url ile kaydeder."""
    new, seen = 0, set()
    for r in records:
        guid = f"{name}:{r['id']}"
        seen.add(guid)
        if con.execute("SELECT 1 FROM ilan WHERE guid=?", (guid,)).fetchone():
            con.execute("UPDATE ilan SET last_seen=?, active=1 WHERE guid=?", (now, guid))
            continue
        detail = {"title": r["title"], "requirements_text": r.get("text") or "", "positions": [],
                  "needs_review": ["kadro_ayrintisi_yok"]}
        con.execute("""INSERT INTO ilan (guid, source, url, institution, title, ilan_type,
            application_start, application_deadline, list_fp, detail_json, source_refs, active, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (guid, name, r["url"], r.get("institution"), r["title"], r.get("type"), r.get("start"),
             r.get("deadline"), r["title"], json.dumps(detail, ensure_ascii=False),
             json.dumps([{"kind": "page", "url": r["url"], "fetched_at": now}]), now, now))
        new += 1
        print(f"YENI    [{name}] {r['title']}\n         {r['url']}")
    if seen:
        con.execute(f"UPDATE ilan SET active=0 WHERE source=? AND guid NOT IN ({','.join('?' * len(seen))})",
                    (name, *seen))
    return new


# ---------- Ana akış ----------
def run(a) -> int:
    con = dbm.connect(a.db)
    con.ensure_schema()

    if a.reparse:
        print(f"{reparse(con)} ilan yeniden ayrıştırıldı")
        return 0
    n = reparse(con, only_stale=True)   # ayrıştırıcı güncellendiyse eski kayıtları ağsız yenile
    if n:
        print(f"[reparse] {n} ilan yeni ayrıştırıcı sürümüyle (v{kk.PARSER_VERSION}) yenilendi")

    session = None
    if a.list_file:
        with open(a.list_file, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        session = make_session()
        raw = post_json(session, LIST_API, LIST_PAYLOAD)

    items = raw.get("searchIlan", [])
    check_total(raw)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seen, fetched, n_new, n_changed, errors = set(), set(), 0, 0, 0

    for it in items:
        rec = list_record(it)
        guid = rec["guid"]
        seen.add(guid)
        fp = fingerprint(rec)
        row = con.execute("SELECT list_fp FROM ilan WHERE guid=?", (guid,)).fetchone()
        if row is not None and row["list_fp"] == fp:
            con.execute("UPDATE ilan SET last_seen=?, active=1 WHERE guid=?", (now, guid))
            continue
        kind = "yeni" if row is None else "değişti"

        detail, bundle = None, None
        if not a.no_detail and session:
            try:
                detail, bundle = fetch_detail(session, guid)
                fetched.add(guid)
            except Exception as e:
                errors += 1
                print(f"[hata] {guid}: {e}", file=sys.stderr)
                continue  # yazma: sonraki çalışmada tekrar denenir
            finally:
                time.sleep(DELAY)

        refs = [{"kind": "list", "url": LIST_API, "payload": LIST_PAYLOAD, "fetched_at": now}]
        refs += [{**r, "fetched_at": now} for r in (bundle or {}).get("refs", [])]
        con.execute(
            """INSERT INTO ilan (guid, url, source_refs, institution, unit, title, ilan_type, application_start,
                 application_deadline, external_url, list_fp, content_hash, detail_json, raw_json,
                 active, first_seen, last_seen)
               VALUES (:guid,:url,:source_refs,:institution,:unit,:title,:ilan_type,:application_start,
                 :application_deadline,:external_url,:fp,:ch,:dj,:rj,1,:now,:now)
               ON CONFLICT(guid) DO UPDATE SET url=:url, source_refs=:source_refs, institution=:institution, unit=:unit,
                 title=:title, ilan_type=:ilan_type, application_start=:application_start,
                 application_deadline=:application_deadline, external_url=:external_url,
                 list_fp=:fp, content_hash=:ch, detail_json=:dj, raw_json=:rj, active=1,
                 last_seen=:now""",
            {**rec, "fp": fp, "now": now, "source_refs": json.dumps(refs, ensure_ascii=False),
             "ch": detail["content_hash"] if detail else None,
             "dj": json.dumps(detail, ensure_ascii=False) if detail else None,
             "rj": json.dumps({k: v for k, v in bundle.items() if k != "refs"}, ensure_ascii=False) if bundle else None},
        )
        n_new += kind == "yeni"
        n_changed += kind == "değişti"
        describe(kind, rec, detail)

    n_text = 0
    if session and not a.no_detail:
        # Yenileme hatası taramayı başarısız saymaz (ilan zaten kayıtlı); yalnızca loglanır.
        n_text, _ = refresh_details(con, session, sorted(seen - fetched), now)
        n_changed += n_text
        con.commit()

    if session:
        from .sources import REGISTRY
        for name, fetch in REGISTRY.items():
            try:
                n_new += ingest_generic(con, name, fetch(session), now)
            except Exception as e:
                errors += 1
                print(f"[{name}] hata: {e}", file=sys.stderr)

    if items and seen:  # boş liste = muhtemel API sorunu, hepsini pasife alma
        marks = ",".join("?" * len(seen))
        con.execute(f"UPDATE ilan SET active=0 WHERE source='kariyer_kapisi' AND guid NOT IN ({marks})", tuple(seen))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (now, len(items), n_new, n_changed, errors))
    con.commit()
    print(f"\nliste: {len(items)} | yeni: {n_new} | değişen: {n_changed} (metin: {n_text}) | hata: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="SQLite yolu veya postgresql:// adresi (varsayılan: $DATABASE_URL ya da data/kpss.db)")
    ap.add_argument("--list-file", help="kayıtlı liste JSON'u (ağ kullanmaz)")
    ap.add_argument("--no-detail", action="store_true", help="ayrıntı isteklerini atla (test)")
    ap.add_argument("--reparse", action="store_true", help="kayıtlı ham cevapları yeniden ayrıştır")
    sys.exit(run(ap.parse_args()))
