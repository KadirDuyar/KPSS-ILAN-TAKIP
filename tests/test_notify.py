"""Bildirim göndericisi (outbox -> ntfy) için ağsız testler. SQLite üzerinde PostgreSQL tablolarının taklidi."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kpss import db as dbm
from kpss import notify as N

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
TOPIC = "kpss-abcdefghijklmnopqrstuvwx"

SCHEMA = """
CREATE TABLE profiles (user_id TEXT PRIMARY KEY, email TEXT, data TEXT);
CREATE TABLE matches (user_id TEXT, guid TEXT, level TEXT, PRIMARY KEY (user_id, guid));
CREATE TABLE notification_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, guid TEXT, event TEXT,
  payload TEXT, created_at TEXT, sent_at TEXT);
"""


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
        self.user("u1", {"notify": {"ntfy_topic": TOPIC}})
        self.user("u2", {})                                   # konu yok

    def tearDown(self):
        self.con.close()
        os.remove(self.path)

    def add_ilan(self, guid, title, deadline="2026-12-05T23:59:00", active=1):
        self.con.execute("INSERT INTO ilan (guid, url, institution, title, application_deadline, active) VALUES "
                         "(?, ?, 'KURUM', ?, ?, ?)", (guid, f"https://kariyerkapisi.gov.tr/IlanDetay?i={guid}",
                                                      title, deadline, active))

    def user(self, uid, data):
        self.con.execute("INSERT INTO profiles VALUES (?, ?, ?)", (uid, f"{uid}@x", json.dumps(data)))

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
        sent = []
        st = N.run(self.con, NOW, send=lambda t, m: sent.append((t, m)))
        self.assertEqual(st["sent"], 1)
        self.assertEqual(sent[0][0], TOPIC)
        self.assertEqual(sent[0][1]["title"], "✅ Uygun ilan")
        self.assertEqual(sent[0][1]["click"], "https://kariyerkapisi.gov.tr/IlanDetay?i=g1")
        self.assertNotIn("u1@x", json.dumps(sent[0][1], ensure_ascii=False))   # kişisel veri yok
        self.assertEqual(self.unsent(), [])
        self.assertEqual(N.run(self.con, NOW, send=lambda t, m: sent.append(1))["sent"], 0)   # ikinci kez yok

    def test_konusu_olmayana_dokunulmaz(self):
        self.event("u2", "g1")
        st = N.run(self.con, NOW, send=lambda t, m: self.fail("gönderilmemeli"))
        self.assertEqual(st["no_topic"], 1)
        self.assertEqual(self.unsent(), ["g1"])               # ileride FCM gönderici okuyabilsin

    def test_kisa_veya_bozuk_konu_reddedilir(self):
        for bad in ("kisa", "a/../../x" + "a" * 20, "kpss abcdefghijklmnopqrstuvwx", None, 123):
            self.assertIsNone(N.topic_of({"notify": {"ntfy_topic": bad}}), bad)
        self.assertIsNone(N.topic_of({"notify": {"ntfy_topic": TOPIC, "enabled": False}}))

    def test_gecersiz_olaylar_atlanir(self):
        self.event("u1", "eski")
        self.event("u1", "pasif")
        self.event("u1", "g1", age_days=10)
        self.event("u1", "g2", level="no")
        st = N.run(self.con, NOW, send=lambda t, m: self.fail("gönderilmemeli"))
        self.assertEqual(st["skipped"], 4)
        self.assertEqual(self.unsent(), [])

    def test_kontrol_bildirimi_kapaliysa_atlanir(self):
        self.con.execute("UPDATE profiles SET data=? WHERE user_id='u1'",
                         (json.dumps({"notify": {"ntfy_topic": TOPIC}, "preferences": {"notify_check": False}}),))
        self.event("u1", "g1", level="check")
        self.assertEqual(N.run(self.con, NOW, send=lambda t, m: self.fail("gönderilmemeli"))["skipped"], 1)

    def test_hata_olayi_geri_birakir(self):
        self.event("u1", "g1")

        def boom(t, m):
            raise RuntimeError("ağ yok")
        with mock.patch("sys.stderr"):
            st = N.run(self.con, NOW, send=boom)
        self.assertEqual(st["failed"], 1)
        self.assertEqual(self.unsent(), ["g1"])               # sonraki çalışmada tekrar denenir

    def test_cok_olay_tek_ozet(self):
        for i in range(6):
            g = f"x{i}"
            self.add_ilan(g, f"İlan {i}")
            self.event("u1", g)
        sent = []
        N.run(self.con, NOW, send=lambda t, m: sent.append(m))
        self.assertEqual(len(sent), 1)
        self.assertIn("6 yeni bildirim", sent[0]["title"])
        self.assertEqual(self.unsent(), [])

    def test_ayni_ilan_iki_olay_tek_bildirim(self):
        self.event("u1", "g1")
        self.event("u1", "g1", ev="REQUIREMENT_CHANGED")
        sent = []
        N.run(self.con, NOW, send=lambda t, m: sent.append(m))
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.unsent(), [])

    def test_baska_calistirici_aldiysa_gonderilmez(self):
        self.event("u1", "g1")
        real = N._claim

        def racing(con, ids, now_iso):
            con.execute("UPDATE notification_outbox SET sent_at='baskasi' WHERE sent_at IS NULL")
            return real(con, ids, now_iso)
        with mock.patch.object(N, "_claim", racing):
            st = N.run(self.con, NOW, send=lambda t, m: self.fail("çift gönderim"))
        self.assertEqual(st["sent"], 0)

    def test_degisti_mesaji(self):
        self.event("u1", "g1", ev="REQUIREMENT_CHANGED", level="check")
        sent = []
        N.run(self.con, NOW, send=lambda t, m: sent.append(m))
        self.assertEqual(sent[0]["title"], "📝 İlan metni değişti")


if __name__ == "__main__":
    unittest.main()
