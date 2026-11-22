"""Bildirim göndericisi: notification_outbox -> FCM (Firebase Cloud Messaging, ücretsiz).

  python -m kpss.notify [--db postgresql://...] [--dry-run]

Alıcılar: push_tokens tablosundaki etkin cihaz token'ları (mobil uygulama register_push_token() ile kaydeder).
Kullanıcı profilinde data.notify.enabled = false ise bildirim gönderilmez.

Kurallar:
- Cihazı olmayan kullanıcının olaylarına dokunulmaz; cihaz eklenince son MAX_AGE_DAYS günün olayları gelir.
- İki çalıştırıcı (VM + GitHub Actions) aynı anda çalışsa da aynı olay iki kez gönderilmez: olay önce
  "sahiplenilir" (sent_at IS NULL koşullu UPDATE), sonra gönderilir; geçici hatada geri bırakılır.
- FCM "token geçersiz" derse token kapatılır (disabled_at); diğer cihazlara gönderim sürer.
- Bildirimde kişisel veri yok: karar, kurum, ilan başlığı, son başvuru tarihi ve resmî bağlantı.
- FCM yapılandırılmamışsa (FCM_SERVICE_ACCOUNT yok) adım hiçbir olaya dokunmadan atlanır.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from . import db as dbm
from . import fcm

MAX_AGE_DAYS = 7          # daha eski olaylar gönderilmez
MAX_SINGLE = 4            # bir çalışmada kullanıcı başına tek tek gönderilecek en çok bildirim; fazlası tek özet
MAX_FAILURES = 3          # art arda geçici hata: bu çalışmayı bırak (sonraki çalışmada devam)

TITLES = {("NEW_MATCH", "ok"): "✅ Uygun ilan", ("NEW_MATCH", "check"): "⚠️ Kontrol edilecek ilan",
          "REQUIREMENT_CHANGED": "📝 İlan metni değişti"}


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


def enabled(data: dict) -> bool:
    n = (data or {}).get("notify")
    return not (isinstance(n, dict) and n.get("enabled") is False)


def skip_reason(r, data, now: datetime):
    """Gönderilmeyecekse neden (olay işlenmiş sayılır), gönderilecekse None."""
    if not enabled(data):
        return "bildirim kapalı"
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


def _url(r):
    return r["url"] if r["url"] and str(r["url"]).startswith("https://") else None


def message(r) -> dict:
    changed = r["event"] == "REQUIREMENT_CHANGED"
    title = TITLES["REQUIREMENT_CHANGED"] if changed else TITLES.get((r["event"], r["level"]), "KPSS ilanı")
    lines = [r["institution"] or "", (r["title"] or "")[:200]]
    dl = _dt(r["application_deadline"])
    if dl:
        lines.append(f"Son başvuru: {dl:%d.%m.%Y %H:%M}")
    if changed:
        lines.append("Şartlar değişmiş olabilir; eşleşmeni tekrar kontrol et.")
    return {"title": title, "body": "\n".join(x for x in lines if x), "click_url": _url(r),
            "data": {"type": r["event"], "guid": r["guid"], "level": r["level"]}}


def summary(rows) -> dict:
    ok = sum(1 for r in rows if r["level"] == "ok" and r["event"] == "NEW_MATCH")
    rest = len(rows) - ok
    parts = [f"{ok} uygun" if ok else "", f"{rest} kontrol/değişiklik" if rest else ""]
    body = ", ".join(p for p in parts if p) + "\n" + "\n".join(f"• {(r['title'] or '')[:90]}" for r in rows[:8])
    return {"title": f"KPSS: {len(rows)} yeni bildirim", "body": body, "click_url": None,
            "data": {"type": "SUMMARY", "count": len(rows)}}


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


def _disable_token(con, token, why, now_iso):
    con.execute("UPDATE push_tokens SET disabled_at=?, last_error=? WHERE token=?", (now_iso, why[:200], token))
    con.commit()


def deliver(con, sender, tokens, msg, now_iso, stats) -> str:
    """Mesajı kullanıcının bütün cihazlarına gönderir. Dönüş: 'sent' | 'retry' | 'no_device'."""
    ok = retry = False
    for t in tokens:
        try:
            sender.send(t, msg["title"], msg["body"], msg.get("data"), msg.get("click_url"))
            ok = True
        except fcm.TokenGone as e:
            _disable_token(con, t, str(e), now_iso)
            stats["tokens_disabled"] += 1
        except fcm.TemporaryError as e:
            retry = True
            print(f"[notify] geçici FCM hatası: {e}", file=sys.stderr)
    return "sent" if ok else "retry" if retry else "no_device"


def run(con, now=None, sender=None, dry_run=False) -> dict:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    stats = {"sent": 0, "skipped": 0, "failed": 0, "no_device": 0, "tokens_disabled": 0}
    if sender is None and not dry_run:
        if not fcm.configured():
            return {"atlandı": f"FCM yapılandırılmadı ({fcm.ENV})"}
        sender = fcm.Sender()

    tokens = {}
    for t in con.execute("SELECT user_id, token FROM push_tokens WHERE disabled_at IS NULL ORDER BY last_seen_at DESC"):
        tokens.setdefault(str(t["user_id"]), []).append(t["token"])
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
        uid, data = str(r["user_id"]), _json(r["data"])
        why = skip_reason(r, data, now)
        if why:
            if not dry_run:
                _claim(con, [r["id"]], now_iso)
            stats["skipped"] += 1
            continue
        if not tokens.get(uid):
            stats["no_device"] += 1          # dokunma: cihaz eklenince gönderilir
            continue
        by_user.setdefault(uid, []).append(r)

    failures = 0
    for uid, items in by_user.items():
        seen, uniq = set(), []
        for r in items:                      # aynı ilan için birden çok olay -> tek bildirim
            if r["guid"] not in seen:
                seen.add(r["guid"])
                uniq.append(r)
        groups = [([r], message(r)) for r in uniq] if len(uniq) <= MAX_SINGLE else [(uniq, summary(uniq))]
        for group, msg in groups:
            gids = {r["guid"] for r in group}
            ids = [r["id"] for r in items if r["guid"] in gids]      # aynı ilanın diğer olayları da kapanır
            if dry_run:
                print(f"[notify] (deneme) {uid[:8]}… {len(tokens[uid])} cihaz: {msg['title']} | {msg['body'][:80]!r}")
                stats["sent"] += 1
                continue
            ids = _claim(con, ids, now_iso)
            if not ids:
                continue                     # başka çalıştırıcı aldı
            try:
                res = deliver(con, sender, tokens[uid], msg, now_iso, stats)
            except Exception:
                _release(con, ids)           # yapılandırma hatası (401/403 vb.): olayları koru, çalışmayı durdur
                raise
            if res == "sent":
                stats["sent"] += 1
                continue
            _release(con, ids)
            if res == "no_device":
                stats["no_device"] += 1
                break
            stats["failed"] += 1
            failures += 1
            if failures >= MAX_FAILURES:
                return stats
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
    con.ensure_schema()
    print("[notify]", run(con, dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
