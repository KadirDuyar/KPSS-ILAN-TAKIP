"""Kasım 2026 Kariyer Kapısı ilanlarından alınan gerçek satırlarla regresyon testleri.
Her test, canlı veride görülen bir yanlış kararı ya da kaçırılan şartı sabitler.
Çalıştır: python -m unittest discover -s tests -v"""
import json
import unittest
from datetime import datetime

from kpss import matching as M
from kpss import requirements as R

DL = datetime(2026, 12, 5)


def kv(text):
    return {(r["kind"], json.dumps(r["value"], sort_keys=True)) for r in R.extract_text(text)}


def kinds(text):
    return {r["kind"] for r in R.extract_text(text)}


def first(text, kind):
    return next(r for r in R.extract_text(text) if r["kind"] == kind)


def levels(reqs, prof, dl=DL):
    return {r["kind"]: r["level"] for r in R.evaluate_all(reqs, prof, dl)}


class YanlisPozitifYok(unittest.TestCase):
    """Şart olmayan satırlar şart üretmemeli (en tehlikelisi: yanlış "uygun değil")."""

    def test_erkek_adaylar_icin_askerlik_cinsiyet_degil(self):
        for t in ("d) Erkek adaylar için askerlik durumu itibariyle; askerlikle ilgisi bulunmamak veya askerlik "
                  "çağına gelmemiş bulunmak ya da askerlik çağına gelmiş ise muvazzaf askerlik hizmetini yapmış "
                  "yahut ertelenmiş veya yedek sınıfa geçirilmiş olmak,",
                  "3.9. Erkek adayların askerlik bilgileri otomatik olarak gelmektedir.",
                  "7 Erkek Adaylar için askerlikle ilişiğinin olmadığına dair belge"):
            self.assertNotIn("gender", kinds(t), t)

    def test_kimlik_belgesi_ehliyet_sarti_degil(self):
        for t in ("Sözlü Yarışma Sınavına katılmaya hak kazanan tüm adayların kimlik kartı veya sürücü belgesi "
                  "ile sınav yerinde bulunmaları gerekmektedir.",
                  "Adayların kimlik tespitinde kullanılmak üzere yanlarında fotoğraflı ve onaylı bir kimlik belgesini "
                  "(TC Kimlik Kartı, Nüfus Cüzdanı, Ehliyet veya Pasaport) bulundurmaları gerekmektedir."):
            self.assertEqual(kinds(t), set(), t)

    def test_puan_esitligi_ve_belge_yukleme(self):
        self.assertEqual(kinds("Puan eşitliği halinde sırasıyla alanında yüksek lisans mezunu olanlar, İngilizce dil "
                               "puanı yüksek olanlar öncelikle yerleştirilecektir."), set())
        self.assertEqual(kinds("e-Devletteki mezuniyet bilgileri hatalı olan adaylar “Lisans Mezuniyet Belgesi”, "
                               "“Yüksek Lisans Mezuniyet Belgesi” alanlarına mezuniyet belgelerini yükleyecektir."), set())

    def test_mezunlari_icin_ogrenim_sarti_degil(self):
        t = "1.3. 2026 KPSS (B) grubu sınavına girmiş olmak (Ön Lisans mezunları için 2026 KPSS (B) grubu KPSSP93 puanı esas alınacaktır.)"
        self.assertNotIn("education_level", kinds(t))
        self.assertIn("kpss_type", kinds(t))

    def test_bin_kullanici_deneyim_yili_degil(self):
        t = ("a) Kritik verilerin bulunduğu kamu kurumlarında yahut en az 2.000 (bin) iç veya 20.000 (yirmi bin) dış "
             "kullanıcısı bulunan sistem merkezlerinde ağ uzmanı olarak en az 3 (üç) yıl tecrübe sahibi olmak")
        self.assertEqual(first(t, "experience_years")["value"], 3)

    def test_tercihen_sart_degil(self):
        self.assertEqual(kinds("f) Tercihen CISSP, CISM, CEH, OSCP sertifikalarından biri ya da daha fazlasına sahip olmak."), set())

    def test_ikamet_zorunlulugu_sart_degil(self):
        self.assertNotIn("residence_text", kinds("Sözleşme yapmaya hak kazanan adaylar sözleşmede belirtilen görev "
                                                 "yerinde ikamet etmek zorundadır."))


class KacirilanSartlar(unittest.TestCase):
    def test_parantezli_ve_yaziyla_yas(self):
        self.assertIn(("age_max", "35"), kv("- İlan tarihi son günü itibarıyla 35 (otuz beş) yaşından gün almamış olmak."))
        self.assertIn(("age_max", "35"), kv("Giriş sınavının yapıldığı yılın Ocak ayının birinci günü itibarıyla otuz beş yaşını doldurmamış olmak."))
        self.assertIn(("age_max", "35"), kv("- Sınavın yapıldığı 2027 yılının Ocak ayının birinci günü itibariyle 35 (otuzbeş) yaşını doldurmamış olmak,"))
        self.assertIn(("age_max", "40"), kv("6) Kontenjanın kullanılacağı yılın son günü itibarıyla 40 yaşını tamamlamamış olmak,"))
        self.assertIn(("age_min", "18"), kv("b) Son başvuru tarihi itibarıyla 18 yaşını doldurmuş olmak,"))

    def test_ocak_referansli_yas_soft(self):
        r = first("Giriş sınavının yapıldığı yılın Ocak ayının birinci günü itibarıyla otuz beş yaşını doldurmamış olmak.", "age_max")
        self.assertTrue(r.get("soft"))

    def test_dogum_tarihi_sarti(self):
        r = first("- 01 Ocak 1991 ve sonrası doğumlu olmak.", "birth_after")
        self.assertEqual(r["value"], {"date": "1991-01-01", "inclusive": True})
        t = "2.1.2. 2027 yılı Ocak ayının birinci günü itibarıyla 35 (otuz beş) yaşını doldurmamış olmak. (01.01.1992 tarihinden sonra doğmuş olmak.)"
        self.assertEqual(first(t, "birth_after")["value"], {"date": "1992-01-01", "inclusive": False})
        self.assertNotIn("age_max", kinds(t))           # doğum tarihi şartı daha kesin
        t = "d) (1 Ocak 2026) 35 (otuz beş) yaşını doldurmamış olması (01/01/1991 tarihinde veya bu tarihten sonra doğmuş olmak),"
        self.assertEqual(first(t, "birth_after")["value"], {"date": "1991-01-01", "inclusive": True})
        t = "4- Ocak ayının ilk günü itibariyle otuz beş yaşını doldurmamış olmak (01.01.1991 ve daha sonra doğanlar başvurabilecektir.)"
        self.assertEqual(first(t, "birth_after")["value"]["date"], "1991-01-01")

    def test_parantezli_kpss_puani(self):
        self.assertEqual(first("- 2026 yılı KPSS (B) KPSSP3 puan türünden en az 60 (altmış) almış olmak.", "kpss_min_score")["value"], 60)
        t = ("b. ÖSYM tarafından 2025 veya 2026 yıllarında yapılan Kamu Personeli Seçme Sınavında KPSSP13, KPSSP33 veya "
             "KPSSP48 puan türlerinin herhangi birinden en az 80 (seksen) puan almış olmak.")
        self.assertEqual(first(t, "kpss_min_score")["value"], 80)
        self.assertEqual(first(t, "kpss_year")["value"], 2025)
        self.assertEqual(first(t, "kpss_type")["value"], ["P13", "P33", "P48"])

    def test_sinav_basari_notu_kpss_degil(self):
        self.assertNotIn("kpss_min_score", kinds("Sınav başarı notu 100 (yüz) puan üzerinden en az 70 (yetmiş)’tir."))

    def test_dil_puani_uzak_yazilmis(self):
        t = ("c) YDS veya e-YDS - İngilizce dilinden son başvuru tarihi itibarıyla son 5 (beş) yıl içerisinde en az 70 "
             "(yetmiş) puan almış veya buna denk kabul edilen uluslararası geçerliliği bulunan belgeye sahip olmak.")
        v = first(t, "language")["value"]
        self.assertEqual((v["exam"], v["min"], v["equiv"]), ("YDS", 70, True))
        self.assertEqual(first("YDS/e-YDS Taban Puanı: 70", "language")["value"]["min"], 70)

    def test_parantezli_ehliyet(self):
        self.assertEqual(first("Niteliği: Çevre mühendisliği lisans programından mezun olmak ve en az (B) sınıfı "
                               "sürücü belgesine sahip olmak.", "license")["value"], ["B"])
        self.assertEqual(first("d) En az “B” sınıfı ehliyet sahibi olmak ve aktif olarak araç kullanabilmek.", "license")["value"], ["B"])

    def test_ertelenmis_askerlik(self):
        self.assertTrue(first("e) Askerlikle ilgisi bulunmamak, askerlik çağına gelmişse muvazzaf askerlik hizmetini "
                              "yapmış yahut ertelenmiş veya yedek sınıfa geçirilmiş olmak.", "military")["value"]["postpone_ok"])
        self.assertFalse(first("- Askerliğini yapmış olmak veya askerlikten muaf olmak,", "military")["value"]["postpone_ok"])


class VeyaMantigi(unittest.TestCase):
    """Aynı türden birden çok şart "veya" sayılır; biri tutmuyor diye ilan elenmez."""

    def test_farkli_satirlarda_puan_turleri(self):
        reqs = R.extract_position({"requirements_text": "KPSS P4 puan türü (hukuk mezunları)\nKPSS P33 veya KPSS P38 puan türü"},
                                  {}, "A Grubu Memur (Kariyer Meslek)")
        types = [r for r in reqs if r["kind"] == "kpss_type"]
        self.assertEqual(len(types), 1)
        self.assertEqual(types[0]["value"], ["P4", "P33", "P38"])
        prof = {"exams": {"kpss": {"scores": {"P4": 85.0}}}}
        self.assertEqual(levels(reqs, prof)["kpss_type"], "ok")

    def test_alternatif_ogrenim_duzeyleri(self):
        t = ("a) Tablo 2’de belirtilen bölümlerinden lisans düzeyinde mezun olmak\n"
             "Tablo 2’de belirtilen öğrenim dalları veya bu dallarla ilgili alanlarda yüksek lisans mezunu olmak,\n"
             "Tablo 2’de belirtilen öğrenim dalları veya bu dallarla ilgili alanlarda doktora mezunu olmak,")
        reqs = R.extract_position({"requirements_text": t}, {}, "Sözleşmeli Personel İlanları")
        edu = [r for r in reqs if r["kind"] == "education_level"]
        self.assertEqual(len(edu), 1)
        self.assertEqual(edu[0]["value"], 3)
        prof = {"education": [{"level": "Lisans", "status": "graduated"}]}
        self.assertEqual(levels(reqs, prof)["education_level"], "ok")

    def test_kpss_aranmaksizin_deneyim_yolu_soft(self):
        t = ("2.2.2 KPSS’ye Girme Şartı Aranmaksızın İş Tecrübesi (Deneyim) ile Başvuracak Uzman Personel Adayları İçin Asgari Şartlar\n"
             "Deneyim: Kamu kesimi veya özel kesimde aşağıdaki konularda belgelendirilecek şekilde en az 5 (beş) yıl fiilen çalışmış olmak")
        reqs = R.extract_position({"requirements_text": t}, {"kpss_text_types": ["P1", "P2", "P3"]}, "Sözleşmeli Personel İlanları")
        prof = {"exams": {"kpss": {"scores": {"P3": 85.0}}}, "work": {"experience_years": 1}}
        lv = levels(reqs, prof)
        self.assertEqual(lv["experience_years"], "check")      # 1 < 5 ama alternatif yol: asla "no" değil


class GenelMetin(unittest.TestCase):
    """İlanın genel şartları da değerlendirilir; çok kadrolu ilanda kesin olmayanlar soft olur."""
    GENEL = ("b) Son başvuru tarihi itibarıyla 35 yaşından gün almamış olmak,\n"
             "Herhangi bir Sosyal Güvenlik Kurumundan emeklilik veya yaşlılık aylığı almıyor olmak,\n"
             "En az B sınıfı sürücü belgesine sahip olmak,")

    def test_genel_yas_ve_emeklilik_kesin(self):
        reqs = R.extract_position({"requirements_text": "- Psikoloji lisans programından mezun olmak,"},
                                  {"requirements_text": self.GENEL}, "Sözleşmeli Personel İlanları", 3)
        k = {r["kind"]: r for r in reqs}
        self.assertFalse(k["age_max"].get("soft"))
        self.assertFalse(k["retired_forbidden"].get("soft"))
        self.assertTrue(k["license"].get("soft"))               # çok kadrolu: başka kadronun şartı olabilir
        prof = {"personal": {"birth_date": "1985-01-01"}, "work": {"retired_pension": False},
                "credentials": {"driving_license": []}}
        lv = levels(reqs, prof)
        self.assertEqual(lv["age_max"], "no")
        self.assertEqual(lv["license"], "check")

    def test_tek_kadroda_genel_metin_kesin(self):
        reqs = R.extract_position({"requirements_text": "- Psikoloji lisans programından mezun olmak,"},
                                  {"requirements_text": self.GENEL}, "Sözleşmeli Personel İlanları", 1)
        self.assertFalse(next(r for r in reqs if r["kind"] == "license").get("soft"))

    def test_kadro_metni_onceliklidir(self):
        reqs = R.extract_position({"requirements_text": "Son başvuru tarihi itibariyle 40 yaşından gün almamış olmak"},
                                  {"requirements_text": self.GENEL}, "Sözleşmeli Personel İlanları", 2)
        self.assertEqual([r["value"] for r in reqs if r["kind"] == "age_max"], [40])


class Degerlendirici(unittest.TestCase):
    def test_eksik_puan_turu_check_tam_liste_no(self):
        reqs = [{"kind": "kpss_type", "value": ["P93"], "evidence": ""}]
        prof = {"exams": {"kpss": {"scores": {"P3": 80.0}}}}
        self.assertEqual(levels(reqs, prof)["kpss_type"], "check")
        prof["exams"]["kpss"]["complete"] = True
        self.assertEqual(levels(reqs, prof)["kpss_type"], "no")

    def test_bilinmeyen_tur_varken_dusuk_puan_check(self):
        reqs = [{"kind": "kpss_type", "value": ["P1", "P3"], "evidence": ""},
                {"kind": "kpss_min_score", "value": 70.0, "evidence": ""}]
        prof = {"exams": {"kpss": {"scores": {"P3": 60.0, "P1": None}}}}
        self.assertEqual(levels(reqs, prof)["kpss_type"], "check")
        prof = {"exams": {"kpss": {"scores": {"P3": 60.0, "P1": 65.0}}}}
        self.assertEqual(levels(reqs, prof)["kpss_type"], "no")

    def test_puan_metin_olarak_gelirse_cokmez(self):
        reqs = [{"kind": "kpss_type", "value": ["P3"], "evidence": ""}]
        self.assertEqual(levels(reqs, {"exams": {"kpss": {"scores": {"p3": "75,5"}}}})["kpss_type"], "check")
        self.assertEqual(levels(reqs, {"exams": {"kpss": {"scores": {"p3": "75.5"}}}})["kpss_type"], "ok")

    def test_dogum_tarihi(self):
        reqs = [{"kind": "birth_after", "value": {"date": "1991-01-01", "inclusive": True}, "evidence": ""}]
        self.assertEqual(levels(reqs, {"personal": {"birth_date": "1991-01-01"}})["birth_after"], "ok")
        self.assertEqual(levels(reqs, {"personal": {"birth_date": "1990-12-31"}})["birth_after"], "no")
        reqs[0]["value"]["inclusive"] = False
        self.assertEqual(levels(reqs, {"personal": {"birth_date": "1991-01-01"}})["birth_after"], "no")

    def test_askerlik_erteleme(self):
        ok = [{"kind": "military", "value": {"postpone_ok": True}, "evidence": ""}]
        strict = [{"kind": "military", "value": {"postpone_ok": False}, "evidence": ""}]
        p = {"personal": {"gender": "E", "military": "postponed"}}
        self.assertEqual(levels(ok, p)["military"], "ok")
        self.assertEqual(levels(strict, p)["military"], "no")
        p = {"personal": {"gender": "E", "military": "not_done"}}
        self.assertEqual(levels(ok, p)["military"], "check")    # çağına gelmemiş olabilir
        self.assertEqual(levels(strict, p)["military"], "no")

    def test_soft_hicbir_zaman_no_olmaz(self):
        reqs = [{"kind": "age_max", "value": 30, "evidence": "", "soft": True}]
        self.assertEqual(levels(reqs, {"personal": {"birth_date": "1980-01-01"}})["age_max"], "check")

    def test_dil_esdegeri(self):
        reqs = [{"kind": "language", "value": {"exam": "YDS", "min": 70, "equiv": True}, "evidence": ""}]
        self.assertEqual(levels(reqs, {"exams": {"language": {"YDS": 60}}})["language"], "check")
        reqs[0]["value"]["equiv"] = False
        self.assertEqual(levels(reqs, {"exams": {"language": {"YDS": 60}}})["language"], "no")


class Eslestirme(unittest.TestCase):
    def _row(self, provinces, ilan_type="Sözleşmeli Personel İlanları", deadline="2026-12-05T23:59:00", text=""):
        d = {"requirements_text": "", "positions": [{"unvan": "UZMAN", "provinces": [{"il": provinces, "kontenjan": 1}],
                                                     "requirements_text": text}]}
        return {"ilan_type": ilan_type, "application_deadline": deadline, "application_start": None,
                "detail_json": json.dumps(d), "title": "T", "guid": "g", "url": None}

    def test_il_ascii_ve_belirsiz(self):
        prof = {"preferences": {"provinces": ["izmir"]}}
        now = datetime(2026, 11, 22)
        self.assertEqual(M.evaluate(self._row("İZMİR / KARABAĞLAR"), prof, now)["level"], "check")  # KPSS türü okunamadı
        self.assertNotIn("il uyuşmuyor", M.evaluate(self._row("İZMİR / KARABAĞLAR"), prof, now)["cands"][0][2])
        self.assertIn("il uyuşmuyor", M.evaluate(self._row("ANKARA / ÇANKAYA"), prof, now)["cands"][0][2])
        r = M.evaluate(self._row("BAKANLIK MERKEZ TEŞKİLATI"), prof, now)["cands"][0]
        self.assertEqual(r[0], "check")
        self.assertTrue(any("il belirsiz" in x for x in r[2]))
        r = M.evaluate(self._row("TR33 Bölgesi / (Afyonkarahisar, Kütahya, Manisa, Uşak)"), {"preferences": {"provinces": ["Manisa"]}}, now)
        self.assertNotIn("il uyuşmuyor", r["cands"][0][2])

    def test_ilan_turu_virgullu(self):
        prof = {"preferences": {"ilan_types": ["Sözleşmeli Personel İlanları"]}}
        row = self._row("ANKARA", ilan_type="Sözleşmeli Personel İlanları, Engelli Personel")
        self.assertIsNotNone(M.evaluate(row, prof, datetime(2026, 11, 22)))

    def test_bitis_tarihi_yoksa_yas_bugune_gore(self):
        row = self._row("ANKARA", deadline=None, text="Son başvuru tarihi itibariyle 35 yaşından gün almamış olmak")
        prof = {"personal": {"birth_date": "2000-01-01"}}
        ev = M.evaluate(row, prof, datetime(2026, 11, 22))
        self.assertFalse(any("yaşın" in x and "sınır" in x for x in ev["cands"][0][2]))   # 26 < 35: sorun yok


if __name__ == "__main__":
    unittest.main()
