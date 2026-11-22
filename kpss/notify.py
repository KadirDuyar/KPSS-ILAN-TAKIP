"""Bildirim göndericisi: notification_outbox -> ntfy.

  python -m kpss.notify [--db postgresql://...] [--dry-run]

Her kullanıcı profilinde kendi ntfy konusunu tutar: data.notify = {"ntfy_topic": "...", "enabled": true}.
Konusu olmayan kullanıcının olaylarına dokunulmaz (ileride mobil/FCM gönderici aynı kuyruğu okuyabilir).

Güvenlik:
- ntfy.sh'de konular herkese açıktır; konu adı parola gibidir. Bu yüzden en az 20 karakterlik
  [A-Za-z0-9_-] konu zorunludur (tahmin edilemez, URL'ye bir şey enjekte edilemez).
- Bildirimde kişisel veri yok: yalnızca karar, kurum, ilan başlığı, son başvuru tarihi ve resmî bağlantı.
- İki çalıştırıcı (VM + GitHub Actions) aynı anda çalışsa da aynı olay iki kez gönderilmez:
  olay önce "sahiplenilir" (sent_at IS NULL koşullu UPDATE), sonra gönderilir; gönderim başarısızsa geri bırakılır.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from . import db as dbm

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
SITE_URL = os.environ.get("KPSS_SITE_URL", "").rstrip("/")
MAX_AGE_DAYS = 7          # daha eski olaylar gönderilmez (ör. konu sonradan eklendiyse eski birikim)
MAX_SINGLE = 4            # bir çalışmada kullanıcı başına tek tek gönderilecek en çok olay; fazlası tek özet
MAX_FAILURES = 3          # sunucu art arda hata verirse bu çalışmayı bırak

TITLES = {("NEW_MATCH", "ok"): "✅ Uygun ilan", ("NEW_MATCH", "check"): "⚠️ Kontrol edilecek ilan",
          "REQUIREMENT_CHANGED": "📝 İlan metni değişti"}
TAGS = {"ok": ["white_check_mark"], "check": ["warning"], "changed": ["memo"]}


def _json(v):
    if isinstance(v, (dict, list)) or v is None:
        return v or {}
    try:
        return json.loads(v)
    except ValueError:
        return {}


def _dt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def topic_of(data: dict):
    n = (data or {}).get("notify") or {}
    if not isinstance(n, dict) or n.get("enabled") is False:
        return None
    t = n.get("ntfy_topic")
    return t if isinstance(t, str) and TOPIC_RE.match(t) else None


def skip_reason(r, data, now: datetime):
    """Gönderilmeyecekse neden (olay işlenmiş sayılır), gönderilecekse None."""
    created = _dt(r["created_at"])
    if created and now - created > timedelta(days=MAX_AGE_DAYS):
        return "eski"
    if not r["title"] or str(r["active"]) not in ("1", "True", "true"):
        return "ilan kalktı"
    dl = _dt(r["application_deadline"])
    if dl and dl.replace(tzinfo=None) < now.replace(tzinfo=None):
        return "süre doldu"
    level = r["level"]
    if level not in ("ok", "check"):
        return "eşleşme yok"
    if level == "check" and ((data or {}).get("preferences") or {}).get("notify_check") is False:
        return "kontrol bildirimi kapalı"
    return None


def message(r) -> dict:
    changed = r["event"] == "REQUIREMENT_CHANGED"
    title = TITLES["REQUIREMENT_CHANGED"] if changed else TITLES.get((r["event"], r["level"]), "KPSS ilanı")
    lines = [r["institution"] or "", (r["title"] or "")[:200]]
    dl = _dt(r["application_deadline"])
    if dl:
        lines.append(f"Son başvuru: {dl:%d.%m.%Y %H:%M}")
    if changed:
        lines.append("Şartlar değişmiş olabilir; eşleşmeni tekrar kontrol et.")
    msg = {"title": title, "message": "\n".join(x for x in lines if x),
           "tags": TAGS["changed" if changed else r["level"]]}
    if r["url"] and str(r["url"]).startswith("https://"):
        msg["click"] = r["url"]
    return msg


def summary(rows) -> dict:
    ok = sum(1 for r in rows if r["level"] == "ok" and r["event"] == "NEW_MATCH")
    parts = [f"{ok} uygun" if ok else "", f"{len(rows) - ok} kontrol/değişiklik" if len(rows) - ok else ""]
    msg = {"title": f"KPSS: {len(rows)} yeni bildirim", "tags": ["bell"],
           "message": ", ".join(p for p in parts if p) + "\n" +
                      "\n".join(f"• {(r['title'] or '')[:90]}" for r in rows[:8])}
    if SITE_URL.startswith("https://"):
        msg["click"] = SITE_URL
    return msg


def send_ntfy(topic: str, msg: dict, session=None):
    import requests

    r = (session or requests).post(SERVER, json={"topic": topic, **msg}, timeout=15)
    if r.status_code >= 300:
        raise RuntimeError(f"ntfy HTTP {r.status_code}: {r.text[:120]}")


def _claim(con, ids, now_iso) -> list:
    got = []
    for i in ids:
        cur = con.execute("UPDATE notification_outbox SET sent_at=? WHERE id=? AND sent_at IS NULL", (now_iso, i))
        if cur.rowcount == 1:
            got.append(i)
    con.commit()
    return got


def _release(con, ids):
    for i in ids:
        con.execute("UPDATE notification_outbox SET sent_at=NULL WHERE id=?", (i,))
    con.commit()


def run(con, now=None, send=send_ntfy, dry_run=False) -> dict:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    stats = {"sent": 0, "skipped": 0, "failed": 0, "no_topic": 0}
    rows = con.execute(
        """SELECT o.id, o.user_id, o.guid, o.event, o.created_at, p.data,
                  i.title, i.institution, i.url, i.application_deadline, i.active, m.level
           FROM notification_outbox o
           JOIN profiles p ON p.user_id = o.user_id
           LEFT JOIN ilan i ON i.guid = o.guid
           LEFT JOIN matches m ON m.user_id = o.user_id AND m.guid = o.guid
           WHERE o.sent_at IS NULL ORDER BY o.id""").fetchall()
    by_user = {}
    for r in rows:
        data = _json(r["data"])
        topic = topic_of(data)
        if not topic:
            stats["no_topic"] += 1
            continue
        why = skip_reason(r, data, now)
        if why:
            if not dry_run:
                _claim(con, [r["id"]], now_iso)
            stats["skipped"] += 1
            continue
        by_user.setdefault(str(r["user_id"]), (topic, []))[1].append(r)

    failures = 0
    for uid, (topic, items) in by_user.items():
        seen, uniq = set(), []
        for r in items:                        # aynı ilan için birden çok olay -> tek bildirim
            if r["guid"] not in seen:
                seen.add(r["guid"])
                uniq.append(r)
        batches = [([r], message(r)) for r in uniq] if len(uniq) <= MAX_SINGLE else [(uniq, summary(uniq))]
        uniq_ids = {r["id"] for r in uniq}
        dup_ids = [r["id"] for r in items if r["id"] not in uniq_ids]
        for group, msg in batches:
            if dry_run:
                print(f"[notify] (deneme) {uid[:8]}…: {msg['title']} | {msg['message'][:80]!r}")
                stats["sent"] += 1
                continue
            ids = _claim(con, [r["id"] for r in group], now_iso)
            if not ids:
                continue                       # başka çalıştırıcı aldı
            try:
                send(topic, msg)
                stats["sent"] += 1
            except Exception as e:
                _release(con, ids)
                stats["failed"] += 1
                failures += 1
                print(f"[notify] gönderilemedi ({uid[:8]}…): {e}", file=sys.stderr)
                if failures >= MAX_FAILURES:
                    return stats
        if dup_ids and not dry_run:
            _claim(con, dup_ids, now_iso)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--dry-run", action="store_true", help="göndermeden ne gönderileceğini yaz")
    a = ap.parse_args(argv)
    con = dbm.connect(a.db)
    if not con.pg:
        print("notify yalnızca PostgreSQL/Supabase için (DATABASE_URL=postgresql://...).", file=sys.stderr)
        return 2
    print("[notify]", run(con, dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
