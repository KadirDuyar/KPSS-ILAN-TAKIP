"""Şart çıkarıcı + değerlendirici.

extract_position(p, ilan, ilan_type) -> [şart]      şart = {kind, value, evidence}
evaluate_all(şartlar, profil, bitiş)  -> [sonuç]     sonuç = {kind, level, msg, evidence}

Kurallar: metinde açıkça yazmayan şart uydurulmaz. Profilde bilinmeyen alan "check" üretir, asla "no" değil.
Okunamayan özel şart "note" olarak görünür (düzeyi değiştirmez ama kullanıcıya gösterilir).
"""
import re
from datetime import date


_T = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")
EDU = (("yuksek lisans", 4), ("doktora", 5), ("onlisans", 2), ("on lisans", 2), ("lisans", 3), ("lise", 1),
       ("ortaokul", 0), ("ilkokul", 0))
EDU_NAME = {0: "ilköğretim", 1: "lise", 2: "önlisans", 3: "lisans", 4: "yüksek lisans", 5: "doktora"}
# KPSS beklenmeyen ilan türleri (KPSS şartı aranmaz)
NO_KPSS = {"gorevde yukselme ve unvan degisikligi ilanlari", "yurt disi egitim ilanlari", "ogretim uyesi",
           "ogretim gorevlisi", "arastirma gorevlisi", "hakim ve savci ilanlari", "yeterlik sinavi ilanlari",
           "belge basvurusu ilanlari", "tercih ilani", "askeri personel ilanlari"}
DECL = re.compile(r"turk vatandasi|kamu haklarindan mahrum|engel teskil|saglik engeli|akil hastaligi|sabika|"
                  r"affa ugramis|kasten islenen|4/b|sozlesmeli personel calistirilmasina")


def norm(s) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_T).lower()).strip()


def edu_rank(text):
    t = norm(text)
    for key, r in EDU:
        if key in t:
            return r
    return None


def _g(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict) or d.get(k) is None:
            return default
        d = d[k]
    return d


def _age(birth: str, on: date) -> int:
    b = date.fromisoformat(birth)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


_GENERIC = ("lisans", "onlisans", "lise", "dengi", "okul", "mezun", "olmak", "olan", "veya", "yuksek",
            "yuksekogretim", "kurum", "program", "universite", "fakulte", "denk", "yurtici", "yurtdisi",
            "tanin", "ilgili", "herhangi", "bolum", "egitim", "ogretim", "bitirmis", "diploma", "belge")


def _specific_words(n: str) -> list:
    """Genel eğitim kelimeleri çıkarılınca kalan, bölüme özgü sözcükler ("bilgisayar muhendisligi")."""
    return [t for t in re.split(r"\W+", n) if len(t) >= 4 and not t.isdigit() and not t.startswith(_GENERIC)]


# ---------- Çıkarıcı ----------
def _extract_line(line: str, n: str) -> list:
    out = []

    def add(kind, value):
        out.append({"kind": kind, "value": value, "evidence": line[:240]})

    from . import kariyer_kapisi as kk  # geç import: döngüsel bağımlılığı önler
    types = kk.p_types(line)
    if types:
        add("kpss_type", types)
    if types or "kpss" in n:
        m = re.search(r"(?<![a-z\d])(\d{2,3}(?:[.,]\d+)?)\s*(?:ve\s+uzeri\s+)?puan(?!\w*\s+uzerinden)", n)
        if m and not re.search(r"\b(ales|yds|yokdil|kpds)\b", n):
            add("kpss_min_score", float(m.group(1).replace(",", ".")))
        y = re.search(r"(20\d{2})\s*(?:yili\s*)?kpss", n)
        if y:
            add("kpss_year", int(y.group(1)))
    for pat, kind in ((r"(\d{2})\s*yasindan\s*gun\s*almamis", "age_max"),
                      (r"(\d{2})\s*yasini\s*doldurmamis", "age_max"),
                      (r"(\d{2})\s*yasini\s*doldurmus", "age_min")):
        m = re.search(pat, n)
        if m:
            add(kind, int(m.group(1)))
    m = re.search(r"cinsiyet\w*\s*:?\s*(.+)", n)
    scope = m.group(1) if m else (n if re.search(r"\b(?:erkek|kadin)\s+(?:olmak|adaylar|aday\w*|personel)", n) else None)
    if scope:
        e, k = "erkek" in scope, "kadin" in scope   # "kadın ve erkek" -> ikisi de: şart değil
        if e != k:
            add("gender", "E" if e else "K")
    if "mezun" in n:
        r = edu_rank(n)
        if r is not None:
            add("education_level", r)
        if re.search(r"program|bolum|fakulte|alan", n) or _specific_words(n):
            add("department_text", line)
    if re.search(r"askerligini yapmis|askerlikten muaf|askerlik hizmetini|askerlikle ilgisi", n):
        add("military", True)
    if "ogrenci" in n and "basvur" in n and re.search(r"orgun|ogrenci kaydi", n):
        add("student_forbidden", {"formal": True,
                                  "open": bool(re.search(r"acik ogretim|uzaktan", n)) and "yapabilir" not in n})
    if re.search(r"emekli|yaslilik aylig", n) and re.search(r"almiyor|almamak|olmamak|bulunmamak|aylik alan", n):
        add("retired_forbidden", True)
    if re.search(r"(sigortali|sosyal guvenlik\w*|sgk\w*).{0,40}calis(?:miyor|mamak|mayan)", n):
        add("insured_forbidden", True)
    lic = "ehliyet" in n or "surucu belgesi" in n
    if lic:
        classes = []
        for g in re.findall(r"((?:\b[a-gm]\d?\b\s*(?:,|/|ve|veya|ile)?\s*)+)sinif", n):
            classes += [c.upper() for c in re.findall(r"\b([a-gm]\d?)\b", g)]
        add("license", classes)
    if not lic and re.search(r"sertifika|belgesine sahip|belgesi (?:olmak|sahibi)|tescilli|belgelendir|ruhsat", n):
        add("certificate_text", line)
    if re.search(r"deneyim|tecrube|calismis olmak", n):
        m = re.search(r"(\d+)\s*(?:\(.*?\)\s*)?yil", n)
        if m:
            add("experience_years", int(m.group(1)))
        else:
            add("experience_text", line)
    m = re.search(r"\b(yds|yokdil|kpds)\b\D{0,60}?(\d{2,3})", n)
    if m:
        add("language", {"exam": m.group(1).upper(), "min": int(m.group(2))})
    m = re.search(r"\bales\b\D{0,60}?(\d{2,3})", n)
    if m:
        add("ales", int(m.group(1)))
    if "engelli" in n and "engel teskil" not in n and "saglik engeli" not in n:
        p = re.search(r"%\s*(\d{2})|yuzde\s*(\d{2})", n)
        rate = int(next(g for g in p.groups() if g)) if p else None
        add("disability", {"required": True, "min_rate": rate})
    if "eski hukumlu" in n:
        add("former_convict", True)
    if (re.search(r"\bboy(u|lu|\s+uzunlug\w*|\s+olcus\w*)?\b|\bkilo(su|nun|yu)?\b|\bcm\b|santimetre|vucut kitle", n)):
        add("physical_text", line)
    if "ikamet" in n:
        add("residence_text", line)
    if "4/b" in n and "fesih" in n and re.search(r"bir yil", n):
        add("termination_4b", 1)
    if not out and DECL.search(n):
        add("declaration", None)
    return out


def extract_text(text: str) -> list:
    reqs, in_ozel = [], False
    for raw in re.split(r"\n+", text or ""):
        line = re.sub(r"^\d{1,2}[\.\)]\s+", "", re.sub(r"^[\-•–·\s]+", "", raw.strip())).strip()
        if not line:
            continue
        n = norm(line)
        if n.startswith("ozel sart"):
            in_ozel = True
            continue
        if re.match(r"(ilan no|pozisyon|adet|ilan edilen pozisyona)", n):
            continue
        found = _extract_line(line, n)
        if found:
            reqs += found
        elif in_ozel and len(n) > 8:
            reqs.append({"kind": "unparsed", "value": None, "evidence": line[:240]})
    return reqs


extract = extract_text  # kariyer_kapisi.py uyumluluğu


def extract_position(p: dict, ilan: dict = None, ilan_type: str = None) -> list:
    ilan = ilan or {}
    reqs = extract_text(p.get("requirements_text"))
    t = norm(ilan_type)
    if t not in NO_KPSS and not any(r["kind"] == "kpss_type" for r in reqs):
        if ilan.get("kpss_text_types"):
            reqs.append({"kind": "kpss_type", "value": ilan["kpss_text_types"], "evidence": "ilan metni (genel)"})
        else:
            reqs.append({"kind": "kpss_unknown", "value": ilan.get("kpss_group"), "evidence": ""})
    if t == "engelli isci":
        reqs.append({"kind": "disability", "value": {"required": True, "min_rate": None},
                     "evidence": "ilan türü: Engelli İşçi"})
    if t == "eski hukumlu isci":
        reqs.append({"kind": "former_convict", "value": True, "evidence": "ilan türü: Eski Hükümlü İşçi"})
    return reqs


# ---------- Profil ----------
def normalize_profile(p: dict) -> dict:
    """Eski düz (v1) profili v2 yapısına çevirir; v2 olduğu gibi döner."""
    if "personal" in p or "preferences" in p:
        return p
    return {
        "name": p.get("name"), "telegram_chat_id": p.get("telegram_chat_id"),
        "personal": {"birth_date": p.get("birth_date"), "gender": p.get("gender")},
        "education": [{"level": p["education_level"], "status": "graduated"}] if p.get("education_level") else [],
        "exams": {"kpss": {"scores": p.get("kpss") or {}}},
        "preferences": {k: p.get(k) for k in ("provinces", "ilan_types", "keywords_include",
                                              "keywords_exclude", "notify_check") if k in p},
    }


# ---------- Değerlendirici ----------
def _res(kind, level, msg, req=None):
    return {"kind": kind, "level": level, "msg": msg, "evidence": (req or {}).get("evidence", "")}


def _overlap(text_n: str, name: str) -> bool:
    nn = norm(name)
    if not nn:
        return False
    if nn in text_n:
        return True
    toks = {t for t in re.split(r"\W+", nn) if len(t) >= 5}
    return len(toks & set(re.split(r"\W+", text_n))) >= 2


def evaluate_all(reqs: list, prof: dict, deadline) -> list:
    out = []
    on = deadline.date() if hasattr(deadline, "date") else deadline
    minsc = next((r["value"] for r in reqs if r["kind"] == "kpss_min_score"), None)
    has_type = any(r["kind"] in ("kpss_type", "kpss_unknown") for r in reqs)
    scores = _g(prof, "exams", "kpss", "scores", default={}) or {}

    for r in reqs:
        k, v = r["kind"], r["value"]
        if k == "kpss_type":
            have = [t for t in v if t in scores]
            known = [scores[t] for t in have if scores[t] is not None]
            if not scores:
                out.append(_res(k, "check", "KPSS puanın girilmemiş", r))
            elif not have:
                out.append(_res(k, "no", f"{'/'.join(v)} puanın yok", r))
            elif not known:
                out.append(_res(k, "check", f"{have[0]} puanı girilmemiş", r))
            elif minsc is not None and max(known) < minsc:
                out.append(_res(k, "no", f"puan {max(known):g} < {minsc:g}", r))
            else:
                t = max(have, key=lambda x: scores[x] or 0)
                out.append(_res(k, "ok", f"{t} puanın {scores[t]:g}" + (f" (≥{minsc:g})" if minsc else ""), r))
        elif k == "kpss_unknown":
            out.append(_res(k, "check", f"KPSS ({'/'.join(v)}) grubu; puan türü ilanda belirtilmemiş" if v
                            else "KPSS puan türü okunamadı", r))
        elif k == "kpss_min_score" and not has_type:
            out.append(_res(k, "check", f"asgari puan {v:g}: puan türü belirsiz", r))
        elif k == "kpss_year":
            y = _g(prof, "exams", "kpss", "year")
            if y is None:
                out.append(_res(k, "check", "KPSS yılı girilmemiş", r))
            else:
                out.append(_res(k, "check" if y < v else "ok", f"KPSS yılın {y}, ilan {v} yılı diyor: geçerlilik/şart metnini doğrula", r))
        elif k in ("age_max", "age_min"):
            bd = _g(prof, "personal", "birth_date")
            if not bd:
                out.append(_res(k, "check", "doğum tarihi girilmemiş", r))
                continue
            age = _age(bd, on)
            bad = age >= v if k == "age_max" else age < v
            out.append(_res(k, "no" if bad else "ok", f"yaşın {age} (sınır {v})", r))
        elif k == "gender":
            pg = _g(prof, "personal", "gender")
            if pg is None:
                out.append(_res(k, "check", f"cinsiyet şartı: {v}", r))
            else:
                out.append(_res(k, "ok" if pg == v else "no", f"cinsiyet şartı: {v}", r))
        elif k == "education_level":
            edus = _g(prof, "education", default=[]) or []
            done = [edu_rank(e.get("level")) for e in edus if e.get("status") in (None, "graduated")]
            stud = [edu_rank(e.get("level")) for e in edus if e.get("status") == "student"]
            done, stud = [x for x in done if x is not None], [x for x in stud if x is not None]
            if not done and not stud:
                out.append(_res(k, "check", "öğrenim bilgisi girilmemiş", r))
            elif done and max(done) >= v:
                out.append(_res(k, "ok", f"öğrenim: {EDU_NAME[max(done)]}", r))
            elif stud and max(stud) >= v:
                out.append(_res(k, "check", f"{EDU_NAME[v]} öğrencisisin: mezuniyet şartını kontrol et", r))
            else:
                out.append(_res(k, "no", f"{EDU_NAME[v]} mezuniyeti gerekli", r))
        elif k == "department_text":
            progs = [e.get("program") for e in (_g(prof, "education", default=[]) or []) if e.get("program")]
            n = norm(v)
            if any(_overlap(n, p) for p in progs):
                out.append(_res(k, "ok", "bölüm şartı profilinle eşleşiyor", r))
            else:
                out.append(_res(k, "check", "bölüm şartını oku: " + v[:140], r))
        elif k == "certificate_text":
            certs = _g(prof, "credentials", "certificates")
            n = norm(v)
            if certs and any(_overlap(n, c) for c in certs):
                out.append(_res(k, "ok", "belge şartı profilinle eşleşiyor", r))
            else:
                out.append(_res(k, "check", "belge şartını doğrula: " + v[:140], r))
        elif k == "license":
            have = _g(prof, "credentials", "driving_license")
            if have is None:
                out.append(_res(k, "check", "ehliyet bilgisi girilmemiş", r))
            elif not have:
                out.append(_res(k, "no", "ehliyet gerekli", r))
            elif not v or set(v) & set(have):
                out.append(_res(k, "ok", "ehliyet şartı karşılanıyor", r))
            else:
                out.append(_res(k, "check", f"ehliyet sınıfı {'/'.join(v)} gerekli, sende {'/'.join(have)}", r))
        elif k == "military":
            m, g = _g(prof, "personal", "military"), _g(prof, "personal", "gender")
            if g == "K" or m in ("done", "exempt", "not_applicable"):
                out.append(_res(k, "ok", "askerlik şartı karşılanıyor", r))
            elif m is None:
                out.append(_res(k, "check", "askerlik durumu girilmemiş", r))
            else:
                out.append(_res(k, "no", "askerlik şartı karşılanmıyor", r))
        elif k == "student_forbidden":
            f, o = _g(prof, "student", "formal_enrolled"), _g(prof, "student", "open_distance_enrolled")
            if f is True or (o is True and v["open"]):
                out.append(_res(k, "no", "öğrenci kaydı bu ilanda engel", r))
            elif f is None or (o is None and v["open"]):
                out.append(_res(k, "check", "öğrencilik durumu girilmemiş", r))
            else:
                out.append(_res(k, "ok", "öğrencilik şartı sorun değil", r))
        elif k in ("retired_forbidden", "insured_forbidden"):
            val = _g(prof, "work", "retired_pension" if k == "retired_forbidden" else "insured_now")
            name = "emekli aylığı" if k == "retired_forbidden" else "sigortalı çalışma"
            if val is None:
                out.append(_res(k, "check", f"{name} durumu girilmemiş", r))
            else:
                out.append(_res(k, "no" if val else "ok", f"{name}: {'var' if val else 'yok'}", r))
        elif k in ("disability", "former_convict"):
            label = "engellilik" if k == "disability" else "eski hükümlü"
            path = ("social", "disability", "has") if k == "disability" else ("social", "former_convict")
            if _g(prof, "consent", "special_categories") is not True:
                out.append(_res(k, "check", f"{label} bilgisi paylaşılmamış (isteğe bağlı)", r))
                continue
            has = _g(prof, *path)
            if has is None:
                out.append(_res(k, "check", f"{label} durumu girilmemiş", r))
            elif has is False:
                out.append(_res(k, "no", f"{label} şartı karşılanmıyor", r))
            else:
                need = v.get("min_rate") if isinstance(v, dict) else None
                rate = _g(prof, "social", "disability", "rate")
                if need and rate is None:
                    out.append(_res(k, "check", f"engel oranı ≥%{need} aranıyor, oranın girilmemiş", r))
                elif need and rate < need:
                    out.append(_res(k, "no", f"engel oranı %{rate} < %{need}", r))
                else:
                    out.append(_res(k, "ok", f"{label} şartı karşılanıyor", r))
        elif k == "experience_years":
            ex = _g(prof, "work", "experience_years")
            if ex is None:
                out.append(_res(k, "check", "deneyim yılı girilmemiş", r))
            else:
                out.append(_res(k, "ok" if ex >= v else "no", f"deneyim {ex} yıl (aranan {v})", r))
        elif k == "language":
            sc = _g(prof, "exams", "language", default={}) or {}
            got = sc.get(v["exam"])
            if got is None:
                out.append(_res(k, "check", f"{v['exam']} puanı girilmemiş (aranan {v['min']})", r))
            else:
                out.append(_res(k, "ok" if got >= v["min"] else "no", f"{v['exam']} {got} (aranan {v['min']})", r))
        elif k == "ales":
            sc = _g(prof, "exams", "ales", "score")
            if sc is None:
                out.append(_res(k, "check", "ALES puanı girilmemiş", r))
            else:
                out.append(_res(k, "ok" if sc >= v else "no", f"ALES {sc:g} (aranan {v})", r))
        elif k == "residence_text":
            res = _g(prof, "personal", "residence_province")
            ok = bool(res) and norm(res) in norm(v)
            out.append(_res(k, "ok" if ok else "check", "ikamet şartı: " + ("uyuyor" if ok else v[:140]), r))
        elif k == "termination_4b":
            t = _g(prof, "work", "last_4b_termination")
            if not t:
                out.append(_res(k, "ok", "4/B fesih kaydı yok", r))
            else:
                days = (on - date.fromisoformat(t)).days
                out.append(_res(k, "no" if days < 365 * v else "ok",
                                f"son 4/B fesih {t}: {'1 yıl dolmamış' if days < 365 * v else 'süre dolmuş'}", r))
        elif k == "physical_text":
            out.append(_res(k, "check", "fiziksel şartı oku: " + v[:140], r))
        elif k == "experience_text":
            out.append(_res(k, "check", "deneyim şartını oku: " + v[:140], r))
        elif k == "unparsed":
            out.append(_res(k, "note", "otomatik okunamadı: " + r["evidence"][:140], r))
    return out
