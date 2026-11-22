"""Ayrıntı yenileme (liste değişmeden ilan metni/kadro değişmesi) için ağsız testler."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from kpss import db as dbm
from kpss import tracker as T

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def item(text="Psikoloji mezunu olmak", fetched=None, il="BURSA"):
    d = {"title": "İlan", "requirements_text": "Genel şartlar", "application_deadline": "2026-12-05T23:59:00",
         "positions": [{"unvan": "PSİKOLOG", "requirements_text": text, "quantity": 1,
                        "provinces": [{"il": il, "kontenjan": 1}]}]}
    d["content_hash"] = T.content_fp(d)
    if fetched:
        d["fetched_at"] = fetched
    return d


class ContentFp(unittest.TestCase):
    def test_yalnizca_icerik_degisince_degisir(self):
        a = item()
        b = {**item(), "fetched_at": "2026-01-01T00:00:00+00:00", "parser": 99, "needs_review": ["x"]}
        self.assertEqual(T.content_fp(a), T.content_fp(b))
        self.assertNotEqual(T.content_fp(a), T.content_fp(item(text="Psikoloji mezunu olmak, 30 yaş sınırı")))
        self.assertNotEqual(T.content_fp(a), T.content_fp(item(il="ANKARA")))


class Refresh(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with mock.patch("builtins.print"):
            self.con = dbm.connect(self.path)
        self.con.ensure_schema()
        old = (NOW - timedelta(hours=20)).isoformat(timespec="seconds")
        fresh = (NOW - timedelta(hours=1)).isoformat(timespec="seconds")
        for guid, d in (("eski", item(fetched=old)), ("yeni", item(fetched=fresh)), ("zamansiz", item())):
            self.con.execute("INSERT INTO ilan (guid, title, detail_json, active, first_seen, last_seen) "
                             "VALUES (?, 'İlan', ?, 1, 'x', 'x')", (guid, json.dumps(d, ensure_ascii=False)))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        os.remove(self.path)

    def _fake(self, changed_guids=(), fail=()):
        def fetch(session, guid):
            if guid in fail:
                raise RuntimeError("zaman aşımı")
            d = item(text="Psikoloji mezunu olmak, 30 yaşından gün almamış olmak" if guid in changed_guids else
                     "Psikoloji mezunu olmak", fetched=NOW.isoformat(timespec="seconds"))
            return d, {"detail": {"x": 1}, "alt": [], "refs": [{"kind": "detail", "url": "u", "payload": {}}]}
        return fetch

    def _detail(self, guid):
        return json.loads(self.con.execute("SELECT detail_json FROM ilan WHERE guid=?", (guid,)).fetchone()["detail_json"])

    def test_yalnizca_bayat_olanlar_yenilenir_degisen_sayilir(self):
        with mock.patch.object(T, "fetch_detail", self._fake(changed_guids={"eski"})) as _, \
             mock.patch.object(T.time, "sleep"), mock.patch("builtins.print"):
            changed, errors = T.refresh_details(self.con, None, ["eski", "yeni", "zamansiz"], NOW.isoformat())
        self.assertEqual((changed, errors), (1, 0))
        self.assertIn("30 yaşından", self._detail("eski")["positions"][0]["requirements_text"])
        self.assertEqual(self._detail("zamansiz")["fetched_at"], NOW.isoformat(timespec="seconds"))  # metin aynı: yalnız zaman
        self.assertEqual(self._detail("yeni")["fetched_at"], (NOW - timedelta(hours=1)).isoformat(timespec="seconds"))

    def test_sinir_ve_sira(self):
        calls = []
        fake = self._fake()

        def spy(s, g):
            calls.append(g)
            return fake(s, g)
        with mock.patch.object(T, "fetch_detail", spy), mock.patch.object(T.time, "sleep"):
            T.refresh_details(self.con, None, ["eski", "yeni", "zamansiz"], NOW.isoformat(), limit=1)
        self.assertEqual(calls, ["zamansiz"])        # zaman bilgisi olmayan en önce

    def test_hata_eski_kaydi_korur(self):
        before = self._detail("eski")
        with mock.patch.object(T, "fetch_detail", self._fake(fail={"eski"})), mock.patch.object(T.time, "sleep"), \
             mock.patch("sys.stderr"):
            changed, errors = T.refresh_details(self.con, None, ["eski"], NOW.isoformat())
        self.assertEqual((changed, errors), (0, 1))
        self.assertEqual(self._detail("eski"), before)

    def test_reparse_cekilme_zamanini_korur(self):
        raw = {"detail": {"ilanBaslik": "İlan", "ilanMetni": "Genel şartlar", "bitTarih": "2026-12-05T23:59:00"},
               "alt": []}
        self.con.execute("UPDATE ilan SET raw_json=? WHERE guid='eski'", (json.dumps(raw, ensure_ascii=False),))
        self.con.commit()
        T.reparse(self.con)
        self.assertEqual(self._detail("eski")["fetched_at"], (NOW - timedelta(hours=20)).isoformat(timespec="seconds"))


if __name__ == "__main__":
    unittest.main()
