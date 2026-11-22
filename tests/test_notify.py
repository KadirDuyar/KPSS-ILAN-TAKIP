"""Bildirim göndericisi (outbox -> FCM) için ağsız testler. SQLite üzerinde PostgreSQL tablolarının taklidi."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kpss import db as dbm
from kpss import fcm
from kpss import notify as N

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
TOK = "fcm-token-" + "a" * 40
TOK2 = "fcm-token-" + "b" * 40

SCHEMA = """
CREATE TABLE profiles (user_id TEXT PRIMARY KEY, email TEXT, data TEXT);
CREATE TABLE matches (user_id TEXT, guid TEXT, level TEXT, PRIMARY KEY (user_id, guid));
CREATE TABLE notification_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, guid TEXT, event TEXT,
  payload TEXT, created_at TEXT, sent_at TEXT);
CREATE TABLE push_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, token TEXT UNIQUE, platform TEXT,
  created_at TEXT, last_seen_at TEXT, disabled_at TEXT, last_error TEXT);
"""


class FakeSender:
    def __init__(self, behaviour=None):
        self.sent, self.behaviour = [], behaviour or {}

    def send(self, token, title, body, data=None, click_url=None):
        b = self.behaviour.get(token)
        if b:
            raise b
        self.sent.append({"token": token, "title": title, "body": body, "data": data, "url": click_url})


class Notify(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with mock.patch("builtins.print"):
            self.con = dbm.connect(self.path)
        self.con.ensure_schema()
        self.con.raw.executescript(SCHEMA)
        self.add_ilan("g1", "Bilişim Uzmanı alımı")
        self.add_ilan("g2", "Programcı alımı")
        self.add_ilan("eski", "Süresi geçmiş ilan", deadline="2026-11-01T23:59:00")
        self.add_ilan("pasif", "Kalkmış ilan", active=0)
        self.user("u1", {})
        self.user("u2", {})                                   # cihazı yok
        self.device("u1", TOK)

    def tearDown(self):
        self.con.close()
        os.remove(self.path)

    def add_ilan(self, guid, title, deadline="2026-12-05T23:59:00", active=1):
        self.con.execute("INSERT INTO ilan (guid, url, institution, title, application_deadline, active) VALUES "
                         "(?, ?, 'KURUM', ?, ?, ?)", (guid, f"https://kariyerkapisi.gov.tr/IlanDetay?i={guid}",
                                                      title, deadline, active))

    def user(self, uid, data):
        self.con.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?, ?)", (uid, f"{uid}@x", json.dumps(data)))

    def device(self, uid, token, last_seen="2026-11-22T10:00:00"):
        self.con.execute("INSERT INTO push_tokens (user_id, token, platform, last_seen_at) VALUES (?, ?, 'android', ?)",
                         (uid, token, last_seen))
        self.con.commit()

    def event(self, uid, guid, level="ok", ev="NEW_MATCH", age_days=0):
        self.con.execute("INSERT OR REPLACE INTO matches VALUES (?, ?, ?)", (uid, guid, level))
        created = (NOW - timedelta(days=age_days)).isoformat()
        self.con.execute("INSERT INTO notification_outbox (user_id, guid, event, payload, created_at) VALUES "
                         "(?, ?, ?, '{}', ?)", (uid, guid, ev, created))
        self.con.commit()

    def unsent(self):
        return [r["guid"] for r in self.con.execute("SELECT guid FROM notification_outbox WHERE sent_at IS NULL")]

    def test_gonderir_ve_isaretler(self):
        self.event("u1", "g1")
        s = FakeSender()
        st = N.run(self.con, NOW, sender=s)
        self.assertEqual(st["sent"], 1)
        m = s.sent[0]
        self.assertEqual((m["token"], m["title"]), (TOK, "✅ Uygun ilan"))
        self.assertEqual(m["url"], "https://kariyerkapisi.gov.tr/IlanDetay?i=g1")
        self.assertEqual(m["data"]["guid"], "g1")
        self.assertNotIn("u1@x", json.dumps(m, ensure_ascii=False))       # kişisel veri yok
        self.assertEqual(self.unsent(), [])
        self.assertEqual(N.run(self.con, NOW, sender=s)["sent"], 0)        # ikinci kez yok

    def test_tum_cihazlara_gider(self):
        self.device("u1", TOK2)
        self.event("u1", "g1")
        s = FakeSender()
        N.run(self.con, NOW, sender=s)
        self.assertEqual(sorted(x["token"] for x in s.sent), sorted([TOK, TOK2]))

    def test_cihazi_olmayana_dokunulmaz(self):
        self.event("u2", "g1")
        st = N.run(self.con, NOW, sender=FakeSender())
        self.assertEqual(st["no_device"], 1)
        self.assertEqual(self.unsent(), ["g1"])               # cihaz eklenince gönderilir

    def test_bildirim_kapali(self):
        self.user("u1", {"notify": {"enabled": False}})
        self.event("u1", "g1")
        s = FakeSender()
        self.assertEqual(N.run(self.con, NOW, sender=s)["skipped"], 1)
        self.assertEqual(s.sent, [])

    def test_gecersiz_olaylar_atlanir(self):
        self.event("u1", "eski")
        self.event("u1", "pasif")
        self.event("u1", "g1", age_days=10)
        self.event("u1", "g2", level="no")
        s = FakeSender()
        self.assertEqual(N.run(self.con, NOW, sender=s)["skipped"], 4)
        self.assertEqual((s.sent, self.unsent()), ([], []))

    def test_kontrol_bildirimi_kapaliysa_atlanir(self):
        self.user("u1", {"preferences": {"notify_check": False}})
        self.event("u1", "g1", level="check")
        self.assertEqual(N.run(self.con, NOW, sender=FakeSender())["skipped"], 1)

    def test_gecersiz_token_kapatilir_digerine_gider(self):
        self.device("u1", TOK2)
        self.event("u1", "g1")
        s = FakeSender({TOK: fcm.TokenGone("UNREGISTERED")})
        st = N.run(self.con, NOW, sender=s)
        self.assertEqual((st["sent"], st["tokens_disabled"]), (1, 1))
        self.assertEqual([x["token"] for x in s.sent], [TOK2])
        dis = self.con.execute("SELECT disabled_at FROM push_tokens WHERE token=?", (TOK,)).fetchone()["disabled_at"]
        self.assertIsNotNone(dis)

    def test_tek_token_gecersizse_olay_bekler(self):
        self.event("u1", "g1")
        st = N.run(self.con, NOW, sender=FakeSender({TOK: fcm.TokenGone("UNREGISTERED")}))
        self.assertEqual(st["sent"], 0)
        self.assertEqual(self.unsent(), ["g1"])               # yeni cihaz kaydolunca gönderilir

    def test_gecici_hata_geri_birakir(self):
        self.event("u1", "g1")
        with mock.patch("sys.stderr"):
            st = N.run(self.con, NOW, sender=FakeSender({TOK: fcm.TemporaryError("HTTP 503")}))
        self.assertEqual(st["failed"], 1)
        self.assertEqual(self.unsent(), ["g1"])

    def test_yapilandirma_hatasi_olaylari_korur(self):
        self.event("u1", "g1")
        with self.assertRaises(RuntimeError):
            N.run(self.con, NOW, sender=FakeSender({TOK: RuntimeError("FCM HTTP 403")}))
        self.assertEqual(self.unsent(), ["g1"])

    def test_fcm_yoksa_atlanir(self):
        self.event("u1", "g1")
        with mock.patch.object(fcm, "configured", return_value=False):
            st = N.run(self.con, NOW)
        self.assertIn("atlandı", st)
        self.assertEqual(self.unsent(), ["g1"])

    def test_cok_olay_tek_ozet(self):
        for i in range(6):
            g = f"x{i}"
            self.add_ilan(g, f"İlan {i}")
            self.event("u1", g)
        s = FakeSender()
        N.run(self.con, NOW, sender=s)
        self.assertEqual(len(s.sent), 1)
        self.assertIn("6 yeni bildirim", s.sent[0]["title"])
        self.assertEqual(self.unsent(), [])

    def test_ayni_ilan_iki_olay_tek_bildirim(self):
        self.event("u1", "g1")
        self.event("u1", "g1", ev="REQUIREMENT_CHANGED")
        s = FakeSender()
        N.run(self.con, NOW, sender=s)
        self.assertEqual(len(s.sent), 1)
        self.assertEqual(self.unsent(), [])

    def test_baska_calistirici_aldiysa_gonderilmez(self):
        self.event("u1", "g1")
        real = N._claim

        def racing(con, ids, now_iso):
            con.execute("UPDATE notification_outbox SET sent_at='baskasi' WHERE sent_at IS NULL")
            return real(con, ids, now_iso)
        s = FakeSender()
        with mock.patch.object(N, "_claim", racing):
            N.run(self.con, NOW, sender=s)
        self.assertEqual(s.sent, [])

    def test_degisti_mesaji(self):
        self.event("u1", "g1", ev="REQUIREMENT_CHANGED", level="check")
        s = FakeSender()
        N.run(self.con, NOW, sender=s)
        self.assertEqual(s.sent[0]["title"], "📝 İlan metni değişti")


class FcmHataSiniflama(unittest.TestCase):
    """fcm.Sender HTTP cevaplarını doğru sınıflamalı (google-auth olmadan)."""

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def sender(self, resp):
        s = object.__new__(fcm.Sender)
        s.url = "https://fcm.googleapis.com/v1/projects/p/messages:send"
        s.http = mock.Mock()
        s.http.post.return_value = resp
        return s

    def err(self, code, status, ecode=None, msg=""):
        details = [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": ecode}] if ecode else []
        return self.Resp(code, {"error": {"status": status, "message": msg, "details": details}})

    def test_basarili_ve_govde(self):
        s = self.sender(self.Resp(200, {"name": "x"}))
        s.send(TOK, "Başlık", "Metin", {"guid": "g1", "count": 3}, "https://kariyerkapisi.gov.tr/")
        body = s.http.post.call_args.kwargs["json"]["message"]
        self.assertEqual(body["token"], TOK)
        self.assertEqual(body["data"], {"guid": "g1", "count": "3", "url": "https://kariyerkapisi.gov.tr/"})
        self.assertEqual(body["android"]["notification"]["channel_id"], "ilanlar")

    def test_siniflama(self):
        with self.assertRaises(fcm.TokenGone):
            self.sender(self.err(404, "NOT_FOUND", "UNREGISTERED")).send(TOK, "t", "b")
        with self.assertRaises(fcm.TokenGone):
            self.sender(self.err(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT",
                                 "The registration token is not a valid FCM registration token")).send(TOK, "t", "b")
        with self.assertRaises(fcm.TemporaryError):
            self.sender(self.err(503, "UNAVAILABLE", "UNAVAILABLE")).send(TOK, "t", "b")
        with self.assertRaises(fcm.TemporaryError):
            self.sender(self.err(429, "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED")).send(TOK, "t", "b")
        with self.assertRaises(fcm.TokenGone):           # token başka Firebase projesine ait
            self.sender(self.err(403, "PERMISSION_DENIED", "SENDER_ID_MISMATCH")).send(TOK, "t", "b")
        with self.assertRaises(RuntimeError) as cm:      # servis hesabı izni yok: çalışmayı durdur
            self.sender(self.err(403, "PERMISSION_DENIED")).send(TOK, "t", "b")
        self.assertNotIsInstance(cm.exception, (fcm.TokenGone, fcm.TemporaryError))


if __name__ == "__main__":
    unittest.main()
