"""Ağsız birim testleri. Çalıştır: python -m unittest discover -s tests -v
Metinler gerçek ilanlardan (BTÜ Psikolog, Kasım 2026) alınmıştır."""
import unittest
from datetime import datetime

from kpss import requirements as R

BTU_GENEL = """5. Herhangi bir Sosyal Güvenlik Kurumundan emeklilik veya yaşlılık aylığı almıyor olmak,
6. Kamu görevinden çıkarılmamış olmak,
7. Başvuracak adayların durumları itibariyle; 657 sayılı Devlet Memurları Kanunu’nun 4/B maddesinin "Bu şekilde istihdam edilenler, hizmet sözleşmesi esaslarına aykırı hareket etmesi nedeniyle kurumlarınca sözleşmelerinin feshedilmesi veya sözleşme dönemi içerisinde Cumhurbaşkanı kararı ile belirlenen istisnalar hariç sözleşmeyi tek taraflı feshetmeleri halinde, fesih tarihinden itibaren bir yıl geçmedikçe kurumların sözleşmeli personel pozisyonlarında istihdam edilemezler." hükmüne uygun olmak.
- Türk Vatandaşı olmak,"""
BTU_KADRO = """ÖZEL ŞARTLAR
- Psikoloji lisans programından mezun olmak,
- Belgelendirmek şartıyla Sağlık Bakanlığı tarafından klinik psikolog olarak tescilli olmak,
- 2026 yılı KPSS(P3)’ten en az 70 puan almış olmak,
- Son başvuru tarihi itibariyle 35 yaşından gün almamış olmak,
- Askerliğini yapmış olmak veya askerlikten muaf olmak,
- Yükseköğretim kurumlarının örgün eğitim programlarında aktif veya pasif öğrenci kaydı bulunan adaylar başvuru yapamaz (Açık öğretim ve uzaktan öğretim programlarında aktif veya pasif öğrenci kaydı bulunanlar başvuru yapabilir)
- Görevini yerine getirmesine engel teşkil edecek herhangi bir sağlık engeli ile akıl hastalığı bulunmamak."""
DL = datetime(2026, 11, 27)


def kinds(text):
    return {r["kind"] for r in R.extract_text(text)}


def lv(text, prof):
    return [(x["kind"], x["level"]) for x in R.evaluate_all(R.extract_text(text), R.normalize_profile(prof), DL)]


FULL = {"name": "t", "personal": {"birth_date": "1995-05-01", "gender": "E", "military": "done"},
        "education": [{"level": "Lisans", "status": "graduated", "program": "Psikoloji"}],
        "exams": {"kpss": {"year": 2026, "scores": {"P3": 75.0}}},
        "student": {"formal_enrolled": False, "open_distance_enrolled": False},
        "work": {"retired_pension": False, "insured_now": False, "experience_years": 1},
        "credentials": {"driving_license": ["B"], "certificates": ["Klinik Psikolog"]},
        "social": {"disability": {"has": False}}, "consent": {"special_categories": True},
        "preferences": {}}


class Extract(unittest.TestCase):
    def test_btu_genel(self):
        k = kinds(BTU_GENEL)
        self.assertTrue({"retired_forbidden", "termination_4b"} <= k)

    def test_btu_kadro(self):
        got = {r["kind"]: r for r in R.extract_text(BTU_KADRO)}
        for need in ("education_level", "department_text", "certificate_text", "kpss_type", "kpss_min_score",
                     "kpss_year", "age_max", "military", "student_forbidden"):
            self.assertIn(need, got)
        self.assertEqual((got["kpss_type"]["value"], got["kpss_min_score"]["value"], got["age_max"]["value"]),
                         (["P3"], 70.0, 35))
        self.assertEqual(got["student_forbidden"]["value"], {"formal": True, "open": False})
        self.assertIn("klinik psikolog", R.norm(got["certificate_text"]["evidence"]))

    def test_saglik_engeli_engelli_degil(self):
        self.assertNotIn("disability", kinds("herhangi bir sağlık engeli ile akıl hastalığı bulunmamak."))

    def test_ehliyet(self):
        r = R.extract_text("B sınıfı sürücü belgesine sahip olmak")[0]
        self.assertEqual((r["kind"], r["value"]), ("license", ["B"]))
        self.assertEqual(R.extract_text("A2 ve C sınıfı sürücü belgesine sahip olmak")[0]["value"], ["A2", "C"])

    def test_deneyim_dil(self):
        self.assertEqual(R.extract_text("Kamuda en az 3 yıl deneyim sahibi olmak")[0]["value"], 3)
        v = R.extract_text("YDS’den en az 70 puan almış olmak")[0]["value"]
        self.assertEqual((v["exam"], v["min"]), ("YDS", 70))

    def test_bolum_genel_degil(self):
        self.assertIn("department_text", kinds("Bilgisayar mühendisliği mezunu olmak"))
        self.assertNotIn("department_text", kinds("Lisans mezunu olmak"))
        self.assertNotIn("department_text", kinds("Lise veya dengi okul mezunu olmak"))


class Bos(unittest.TestCase):
    """Formdan gelen null alanlar hiçbir şartta çökme yaratmamalı."""
    def test_null_puanlar(self):
        txt = "YDS’den en az 70 puan almış olmak\nALES’ten en az 70 puan almış olmak\nKamuda en az 3 yıl deneyim sahibi olmak"
        p = {**FULL, "exams": {"kpss": {"scores": {"P3": None}, "year": None},
                               "language": {"YDS": None, "YOKDIL": None}, "ales": {"score": None}},
             "work": {"experience_years": None, "last_4b_termination": None}}
        res = lv(txt, p)
        self.assertTrue(all(l in ("check", "ok", "note") for _, l in res), res)
        self.assertIn(("language", "check"), res)


class Adli(unittest.TestCase):
    """Adli Tıp / İnfaz ve Koruma Memuru (Kasım 2026) metinleri: cinsiyet ve fiziksel şartlar okunmalı."""
    TXT = """Cinsiyeti erkek olmak.
170 cm. 'den kısa boylu olmamak.
Boy uzunluğunun santimetre cinsinden son iki rakamı ile kilosu arasındaki fark 13'ten fazla olmamak."""

    def test_cinsiyet_ve_fiziksel(self):
        k = [(r["kind"], r["value"]) for r in R.extract_text(self.TXT)]
        self.assertIn(("gender", "E"), k)
        self.assertEqual(sum(1 for x, _ in k if x == "physical_text"), 2)

    def test_hicbir_zaman_uygun_cikmaz(self):
        res = lv(self.TXT, FULL)               # profilde cinsiyet E olsa bile boy/kilo kontrol gerektirir
        self.assertIn(("gender", "ok"), res)
        self.assertEqual([l for k, l in res if k == "physical_text"], ["check", "check"])
        p = {**FULL, "personal": {**FULL["personal"], "gender": "K"}}
        self.assertIn(("gender", "no"), lv(self.TXT, p))
        p = {**FULL, "personal": {"birth_date": "1995-05-01"}}   # cinsiyet belirtilmemiş -> check
        self.assertIn(("gender", "check"), lv(self.TXT, p))

    def test_yanlis_pozitif_yok(self):
        self.assertNotIn("physical_text", kinds("Sözleşme süresi boyunca görev yapmak, kilometre bazlı ödeme"))
        self.assertNotIn("gender", kinds("Kadın ve erkek adaylar başvurabilir"))


class Evaluate(unittest.TestCase):
    def test_profil_uyuyor(self):
        res = lv(BTU_KADRO + "\n" + BTU_GENEL, FULL)
        self.assertNotIn("no", [l for _, l in res])
        self.assertIn(("kpss_type", "ok"), res)
        self.assertIn(("age_max", "ok"), res)

    def test_ogrenci(self):
        p = {**FULL, "student": {"formal_enrolled": True, "open_distance_enrolled": False}}
        self.assertIn(("student_forbidden", "no"), lv(BTU_KADRO, p))
        p = {**FULL, "student": {}}          # bilinmiyor -> check, asla no
        self.assertIn(("student_forbidden", "check"), lv(BTU_KADRO, p))

    def test_bilinmeyen_check(self):
        p = {**FULL, "work": {}}
        res = lv(BTU_GENEL, p)
        self.assertIn(("retired_forbidden", "check"), res)

    def test_deneyim_ve_fesih(self):
        self.assertIn(("experience_years", "no"), lv("en az 3 yıl deneyim sahibi olmak", FULL))
        p = {**FULL, "work": {**FULL["work"], "last_4b_termination": "2026-06-01"}}
        self.assertIn(("termination_4b", "no"), lv(BTU_GENEL, p))

    def test_puan_ve_yil(self):
        p = {**FULL, "exams": {"kpss": {"year": 2024, "scores": {"P3": 65.0}}}}
        res = lv(BTU_KADRO, p)
        self.assertIn(("kpss_type", "no"), res)        # 65 < 70
        self.assertIn(("kpss_year", "check"), res)     # yıl farkı asla 'no' olmaz

    def test_yas_siniri(self):
        p = {**FULL, "personal": {**FULL["personal"], "birth_date": "1990-01-01"}}
        self.assertIn(("age_max", "no"), lv(BTU_KADRO, p))

    def test_ozel_veri_onaysiz(self):
        p = {**FULL, "consent": {"special_categories": False}}
        res = [x for x in R.evaluate_all(R.extract_position({"requirements_text": ""}, {}, "Engelli İşçi"),
                                         R.normalize_profile(p), DL)]
        self.assertEqual([x["level"] for x in res if x["kind"] == "disability"], ["check"])

    def test_ilan_turu(self):
        r = R.extract_position({"requirements_text": ""}, {}, "Eski Hükümlü İşçi")
        self.assertIn("former_convict", {x["kind"] for x in r})


if __name__ == "__main__":
    unittest.main()
