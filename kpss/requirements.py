"""Şart çıkarıcı + değerlendirici.

extract_position(p, ilan, ilan_type, n_positions) -> [şart]   şart = {kind, value, evidence, soft?, scope?}
evaluate_all(şartlar, profil, bitiş)              -> [sonuç]  sonuç = {kind, level, msg, evidence}

Kurallar:
- Metinde açıkça yazmayan şart uydurulmaz. Profilde bilinmeyen alan "check" üretir, asla "no" değil.
- "soft" şart (koşullu satır, ilanın genel metni, alternatif başvuru yolu, belirsiz referans tarihi)
  hiçbir zaman "no" üretmez; karşılanmıyorsa "check" olur.
- Aynı türden birden çok şart (ör. farklı tablolar için farklı puan türleri) "veya" sayılır ve
  en esnek hâliyle tek şarta indirgenir: yanlışlıkla elemektense kontrol ettirmek tercih edilir.
- Okunamayan özel şart "note" olarak görünür (düzeyi değiştirmez ama kullanıcıya gösterilir).
"""
import re
from datetime import date

_T = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")
EDU_NAME = {0: "ilköğretim", 1: "lise", 2: "önlisans", 3: "lisans", 4: "yüksek lisans", 5: "doktora"}
# KPSS beklenmeyen ilan türleri (KPSS şartı aranmaz)
NO_KPSS = {"gorevde yukselme ve unvan degisikligi ilanlari", "yurt disi egitim ilanlari", "ogretim uyesi",
           "ogretim gorevlisi", "arastirma gorevlisi", "hakim ve savci ilanlari", "yeterlik sinavi ilanlari",
           "belge basvurusu ilanlari", "tercih ilani", "askeri personel ilanlari"}
DECL = re.compile(r"turk vatandasi|kamu haklarindan mahrum|engel teskil|saglik engeli|akil hastaligi|sabika|"
                  r"affa ugramis|kasten islenen|4/b|sozlesmeli personel calistirilmasina")
# Şart değil, usul/belge/sıralama satırı: hiçbir şart üretmez.
NOISE = re.compile(r"kimlik (?:belgesi|karti|kartlari|numarali)|nufus cuzdan|pasaport|puan esitligi|"
                   r"mezuniyet belgesi|otomatik olarak|otomatik (?:kaydedil|cekil|gel)|web servis|"
                   r"tercih sebeb|tercih neden|tercih edil|oncelikle|oncelik veril|ibraz edemeyen|"
                   r"sinav yerinde|sinava giris belgesi|aday belgesi|\btercihen\b|sistemi uzerinden aktar|"
                   r"zorundadir|zorunda olacak")
# Satır yalnızca bir kesim için geçerliyse (ör. "Erkek adaylar için", "Ön Lisans mezunları için"): soft.
COND = re.compile(r"\b(?:mezunlari|adaylar\w*|personel\w*|pozisyon\w*|unvan\w*|kadro\w*|grubu|bolum\w*)\s+icin\b|"
                  r"ucret tavan|aranmaksizin|bakimindan")
ALT_PATH = re.compile(r"kpss\W{0,2}\w*\s+(?:\w+\s+){0,3}aranmaksizin")
TEXT_KINDS = {"department_text", "certificate_text", "experience_text", "physical_text", "residence_text", "unparsed"}
# İlanın genel metninden çok kadrolu ilanlarda da kesin ("no" üretebilir) kabul edilen türler.
HARD_GENERAL = {"age_max", "age_min", "birth_after", "retired_forbidden", "insured_forbidden",
                "termination_4b", "student_forbidden", "military"}

_ONES = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}
_TENS = {"on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "altmis": 60, "yetmis": 70, "seksen": 80,
         "doksan": 90}
_NUM = (r"(\d{1,3}|\b(?:" + "|".join(_TENS) + r")\s?(?:" + "|".join(_ONES) + r")?)")
_MONTHS = {m: i + 1 for i, m in enumerate(("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz",
                                           "agustos", "eylul", "ekim", "kasim", "aralik"))}


def norm(s) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_T).lower()).strip()


def _num(tok: str) -> int:
    tok = tok.replace(" ", "")
    if tok.isdigit():
        return int(tok)
    for t, tv in _TENS.items():
        if tok.startswith(t):
            return tv + _ONES.get(tok[len(t):], 0)
    raise ValueError(tok)


def edu_levels(text) -> set:
    """Metinde geçen öğrenim düzeyleri ("yüksek lisans" içindeki "lisans" sayılmaz)."""
    t, out = norm(text), set()
    for pat, r in ((r"yuksek ?lisans", 4), (r"doktora", 5), (r"on ?lisans", 2), (r"lisans", 3),
                   (r"\blise\b|\blisesi\b|ortaogretim", 1), (r"ilkogretim|ortaokul|ilkokul", 0)):
        if re.search(pat, t):
            out.add(r)
            t = re.sub(pat, " ", t)
    return out


def edu_rank(text):
    """En düşük düzey (şart için en esnek okuma); profilde tek düzey yazılı olduğundan aynı sonuç."""
    lv = edu_levels(text)
    return min(lv) if lv else None


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


# ---------- Çıkarıcı yardımcıları ----------
def kpss_min_score(n: str):
    """KPSS satırındaki asgari puan: "en az 80 (seksen) puan", "70 ve üzeri puan", "(80) ve üzerinde"."""
    for pat in (r"en az\s*\(?\s*(\d{2,3}(?:[.,]\d+)?)(?![\d.])",
                r"(?<![a-z\d.])(\d{2,3}(?:[.,]\d+)?)\)?\s*(?:\([^)]{0,20}\)\s*)?(?:ve\s+uzeri\w*\s+)?puan(?!\w*\s+uzerinden)",
                r"\((\d{2,3})\)\s*ve\s+uzeri"):
        m = re.search(pat, n)
        if m:
            v = float(m.group(1).replace(",", "."))
            if 40 <= v <= 100:
                return v
    return None


def _kpss_years(n: str) -> list:
    out = []
    for m in re.finditer(r"\b(20\d{2})\b", n):
        tail = n[m.end():m.end() + 70]
        if re.search(r"kpss|kamu personel", tail) and not re.match(r"\s*tarih", tail):
            out.append(int(m.group(1)))
    return out


def _ages(n: str) -> list:
    """[(kind, değer, referans_belirsiz)]"""
    out = []
    pat = _NUM + r"\s*(?:\([^)]{0,25}\)\s*)?yas(?:indan|ini|ina)\s*(gun almamis|doldurmamis|tamamlamamis|bitirmemis|doldurmus|tamamlamis|bitirmis)"
    unsure = bool(re.search(r"ocak ayinin|yilin son gunu|kontenjanin", n))
    for m in re.finditer(pat, n):
        try:
            v = _num(m.group(1))
        except ValueError:
            continue
        if not 15 <= v <= 70:
            continue
        verb = m.group(2)
        if verb in ("gun almamis", "doldurmamis", "tamamlamamis"):
            out.append(("age_max", v, unsure))
        elif verb == "bitirmemis":               # "30 yaşını bitirmemiş" = en çok 30 yaşında
            out.append(("age_max", v + 1, True))
        else:
            out.append(("age_min", v, unsure))
    return out


def _birth_after(n: str):
    """"01.01.1991 ve sonrası doğumlu", "01 Ocak 1996 ve sonrası", "tarihinden sonra doğmuş" -> {date, inclusive}"""
    for m in re.finditer(r"(\d{1,2})[./ ](\d{1,2}|" + "|".join(_MONTHS) + r")[./ ](\d{4})", n):
        tail = n[m.end():m.end() + 70]
        if not re.search(r"sonra", tail) or not re.search(r"dog", tail):
            continue
        mon = int(m.group(2)) if m.group(2).isdigit() else _MONTHS[m.group(2)]
        try:
            d = date(int(m.group(3)), mon, int(m.group(1)))
        except ValueError:
            continue
        inclusive = not re.match(r"\s*tarihinden sonra", tail) or bool(re.search(r"veya bu tarihten", tail))
        return {"date": d.isoformat(), "inclusive": inclusive}
    return None


def _exam_score(n: str, exam_pat: str):
    m = re.search(exam_pat, n)
    if not m:
        return None
    rest = n[m.end():m.end() + 260]
    for pat in (r"en az\s*\(?\s*(\d{2,3})(?![\d.])", r"(?<![\d.])(\d{2,3})(?![\d.])\s*(?:\([^)]{0,20}\)\s*)?puan",
                r"taban puani\s*:?\s*(\d{2,3})"):
        x = re.search(pat, rest)
        if x and 30 <= int(x.group(1)) <= 100:
            return int(x.group(1))
    return None


# ---------- Çıkarıcı ----------
def _extract_line(line: str, n: str) -> list:
    if NOISE.search(n):
        return []
    out = []
    soft = bool(COND.search(n)) and not re.search(r"\bher (?:bir )?(?:pozisyon|kadro|unvan)\w* icin", n)

    def add(kind, value, s=False):
        r = {"kind": kind, "value": value, "evidence": line[:240]}
        if soft or s:
            r["soft"] = True
        out.append(r)

    from . import kariyer_kapisi as kk  # geç import: döngüsel bağımlılığı önler
    types = kk.p_types(line)
    if types:
        add("kpss_type", types)
    if types or "kpss" in n or "kamu personel" in n:
        if not re.search(r"\b(ales|yds|yokdil|kpds)\b", n):
            sc = kpss_min_score(n)
            if sc is not None:
                add("kpss_min_score", sc)
        ys = _kpss_years(n)
        if ys:
            add("kpss_year", min(ys))
    ba = _birth_after(n)
    if ba:
        add("birth_after", ba)
    for kind, v, unsure in _ages(n):
        if kind == "age_max" and ba:
            continue                          # doğum tarihi şartı daha kesin
        if any(r["kind"] == kind for r in out):
            continue                          # "18 yaşını (… meslek okulu için 15)" -> ilki
        add(kind, v, unsure)
    if "askerlik" not in n:
        m = re.search(r"cinsiyet\w*\s*:?\s*(.+)", n)
        scope = m.group(1) if m else (
            n if re.search(r"\b(?:sadece|yalniz(?:ca)?)\s+(?:erkek|kadin)|\b(?:erkek|kadin)\s+olma(?:k|si)\b|"
                           r"\b(?:erkek|kadin)\s+adaylar\w*\s+(?:basvur|alin)", n) else None)
        if scope:
            e, k = "erkek" in scope, "kadin" in scope   # "kadın ve erkek" -> ikisi de: şart değil
            if e != k:
                add("gender", "E" if e else "K")
    if "mezun" in n and "mezunlari icin" not in n and "mezuniyet tarihi" not in n:
        lv = edu_levels(n)
        if lv:
            add("education_level", min(lv))
        if re.search(r"program|bolum|fakulte|alan|dal", n) or _specific_words(n):
            add("department_text", line)
    if "askerlik" in n and re.search(r"yapmis|muaf|ilgisi|ilisig|terhis|ertele|yedek sinif|cagina", n):
        add("military", {"postpone_ok": bool(re.search(r"ertele|tecil|yedek sinif|cagina gelmemis|ilgisi|ilisig", n))})
    if "ogrenci" in n and "basvur" in n and re.search(r"orgun|ogrenci kaydi", n):
        add("student_forbidden", {"formal": True,
                                  "open": bool(re.search(r"acik ogretim|uzaktan", n)) and "yapabilir" not in n})
    if re.search(r"emekli|yaslilik aylig", n) and re.search(r"almiyor|almamak|olmamak|bulunmamak|aylik alan", n):
        add("retired_forbidden", True)
    if re.search(r"(sigortali|sosyal guvenlik\w*|sgk\w*).{0,40}calis(?:miyor|mamak|mayan)", n):
        add("insured_forbidden", True)
    lic = "ehliyet" in n or "surucu belgesi" in n
    if lic:
        n2 = re.sub(r"[()\"'“”‘’«»]", " ", n)
        classes = []
        for g in re.findall(r"((?:\b[a-gm]\d?\b\s*(?:,|/|ve|veya|ile)?\s*)+)sinif", n2):
            classes += [c.upper() for c in re.findall(r"\b([a-gm]\d?)\b", g)]
        add("license", sorted(set(classes)))
    if not lic and re.search(r"sertifika|belgesine sahip|belgesi (?:olmak|sahibi)|tescilli|belgelendir|ruhsat", n):
        add("certificate_text", line)
    if re.search(r"deneyim|tecrube|calismis olmak|fiilen\s+(?:basarili olarak\s+)?calis", n):
        yrs = [int(x) for x in re.findall(r"(?<![\d.,])(\d{1,2})\s*(?:\([^)]{0,15}\)\s*)?yil", n)]
        yrs = [y for y in yrs if 0 < y <= 40]
        if yrs:
            add("experience_years", min(yrs))   # "3 yıl, diğerleri için 5 yıl" -> en esnek
        else:
            add("experience_text", line)
    for exam, pat in (("YDS", r"\b(?:e-)?yds\b"), ("YOKDIL", r"\byokdil\b"), ("KPDS", r"\bkpds\b")):
        v = _exam_score(n, pat)
        if v is not None:
            add("language", {"exam": exam, "min": v,
                             "equiv": bool(re.search(r"esdeger|denk|uluslararasi|veya (?:e-)?yds|yokdil", n))})
            break
    v = _exam_score(n, r"\bales\b")
    if v is not None:
        add("ales", v)
    if "engelli" in n and "engel teskil" not in n and "saglik engeli" not in n:
        p = re.search(r"%\s*(\d{2})|yuzde\s*(\d{2})", n)
        rate = int(next(g for g in p.groups() if g)) if p else None
        add("disability", {"required": True, "min_rate": rate})
    if "eski hukumlu" in n:
        add("former_convict", True)
    if re.search(r"\bboy(u|lu|\s+uzunlug\w*|\s+olcus\w*)?\b|\bkilo(su|nun|yu)?\b|\bcm\b|santimetre|vucut kitle", n):
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
        line = re.sub(r"^\d{1,2}[\.\)]\s+", "", re.sub(r"^[\-•–·*\s]+", "", raw.strip())).strip()
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
        elif in_ozel and len(n) > 8 and not NOISE.search(n):
            reqs.append({"kind": "unparsed", "value": None, "evidence": line[:240]})
    if ALT_PATH.search(norm(text)):       # "KPSS şartı aranmaksızın iş tecrübesiyle": iki yol, ikisi de soft
        for r in reqs:
            if r["kind"].startswith(("kpss", "experience")):
                r["soft"] = True
    return reqs


extract = extract_text  # kariyer_kapisi.py uyumluluğu


def consolidate(reqs: list) -> list:
    """Aynı türden şartları "veya" mantığıyla tek şarta indirir (en esnek hâl)."""
    by, order = {}, []
    for r in reqs:
        if r["kind"] not in by:
            order.append(r["kind"])
        by.setdefault(r["kind"], []).append(r)
    out = []

    def one(rs, value, soft=None):
        r = dict(rs[0])
        r["value"] = value
        s = all(x.get("soft") for x in rs) if soft is None else soft
        r.pop("soft", None)
        if s:
            r["soft"] = True
        return r

    for k in order:
        rs = by[k]
        if k == "declaration":
            continue
        if k in TEXT_KINDS:
            seen = set()
            for r in rs:
                if r["evidence"] not in seen:
                    seen.add(r["evidence"])
                    out.append(r)
            continue
        if len(rs) == 1:
            out.append(rs[0])
            continue
        vals = [r["value"] for r in rs]
        differ = any(v != vals[0] for v in vals)
        if k == "kpss_type":
            u = sorted({t for v in vals for t in v}, key=lambda x: int(x[1:]))
            out.append(one(rs, u))
        elif k in ("education_level", "kpss_min_score", "experience_years", "age_min", "kpss_year", "ales"):
            out.append(one(rs, min(vals), soft=True if differ else None))
        elif k == "age_max":
            out.append(one(rs, max(vals), soft=True if differ else None))
        elif k == "birth_after":
            out.append(one(rs, min(vals, key=lambda v: v["date"]), soft=True if differ else None))
        elif k == "language":
            best = {}
            for v in vals:
                if v["exam"] not in best or v["min"] < best[v["exam"]]["min"]:
                    best[v["exam"]] = v
            for v in best.values():
                out.append(one(rs, v, soft=True if len(best) > 1 or differ else None))
        elif k == "license":
            out.append(one(rs, [] if any(not v for v in vals) else sorted({c for v in vals for c in v})))
        elif k == "gender":
            if not differ:
                out.append(one(rs, vals[0]))
        elif k == "military":
            out.append(one(rs, {"postpone_ok": any((v or {}).get("postpone_ok") for v in vals)}))
        elif k == "student_forbidden":
            out.append(one(rs, {"formal": True, "open": all(v.get("open") for v in vals)}))
        elif k == "disability":
            rates = [v.get("min_rate") for v in vals if v.get("min_rate")]
            out.append(one(rs, {"required": True, "min_rate": min(rates) if rates else None}))
        else:
            out.append(rs[0])
    return out


def extract_position(p: dict, ilan: dict = None, ilan_type: str = None, n_positions: int = 1) -> list:
    """Kadro metni + ilanın genel metni -> birleştirilmiş şart listesi.

    Kadro metni önceliklidir. İlanın genel metninden yalnızca kadroda olmayan türler eklenir; çok kadrolu
    ilanda genel metin başka kadroların şartlarını da içerebildiğinden bunlar (HARD_GENERAL dışında) soft olur.
    """
    ilan = ilan or {}
    reqs = extract_text(p.get("requirements_text"))
    for r in reqs:
        r["scope"] = "kadro"
    have = {r["kind"] for r in reqs}
    gen_text = ilan.get("requirements_text") or ""
    if gen_text and gen_text != p.get("requirements_text"):
        single = n_positions <= 1
        for r in extract_text(gen_text):
            k = r["kind"]
            if k in have or k in ("unparsed", "declaration", "kpss_type"):
                continue
            if not single and k in TEXT_KINDS:
                continue
            r["scope"] = "ilan"
            if not (single or k in HARD_GENERAL):
                r["soft"] = True
            reqs.append(r)
    t = norm(ilan_type)
    if t not in NO_KPSS and not any(r["kind"] == "kpss_type" for r in reqs):
        alt = bool(ALT_PATH.search(norm(gen_text) + " " + norm(p.get("requirements_text"))))
        if ilan.get("kpss_text_types"):
            r = {"kind": "kpss_type", "value": ilan["kpss_text_types"], "evidence": "ilan metni (genel)",
                 "scope": "ilan"}
            if alt or not n_positions <= 1:
                r["soft"] = True
            reqs.append(r)
        else:
            reqs.append({"kind": "kpss_unknown", "value": ilan.get("kpss_group"), "evidence": ""})
    if t == "engelli isci":
        reqs.append({"kind": "disability", "value": {"required": True, "min_rate": None},
                     "evidence": "ilan türü: Engelli İşçi"})
    if t == "eski hukumlu isci":
        reqs.append({"kind": "former_convict", "value": True, "evidence": "ilan türü: Eski Hükümlü İşçi"})
    return consolidate(reqs)


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
SOFT_NOTE = " (koşullu/genel madde: ilanda doğrula)"


def _res(kind, level, msg, req=None):
    req = req or {}
    if level == "no" and req.get("soft"):
        level, msg = "check", msg + SOFT_NOTE
    return {"kind": kind, "level": level, "msg": msg, "evidence": req.get("evidence", "")}


def _overlap(text_n: str, name: str) -> bool:
    nn = norm(name)
    if not nn:
        return False
    if nn in text_n:
        return True
    toks = {t for t in re.split(r"\W+", nn) if len(t) >= 5}
    return len(toks & set(re.split(r"\W+", text_n))) >= 2


def _num_or_none(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def evaluate_all(reqs: list, prof: dict, deadline) -> list:
    out = []
    on = deadline.date() if hasattr(deadline, "date") else deadline
    minr = next((r for r in reqs if r["kind"] == "kpss_min_score"), None)
    minsc = minr["value"] if minr else None
    has_type = any(r["kind"] in ("kpss_type", "kpss_unknown") for r in reqs)
    raw_scores = _g(prof, "exams", "kpss", "scores", default={}) or {}
    scores = {str(k).upper(): _num_or_none(v) for k, v in raw_scores.items()} if isinstance(raw_scores, dict) else {}
    complete = _g(prof, "exams", "kpss", "complete") is True

    for r in reqs:
        k, v = r["kind"], r["value"]
        if k == "kpss_type":
            soft = r.get("soft") or (minr or {}).get("soft")
            rr = {**r, "soft": soft}
            have = [t for t in v if t in scores]
            known = [scores[t] for t in have if scores[t] is not None]
            if not scores:
                out.append(_res(k, "check", "KPSS puanın girilmemiş", r))
            elif not have:
                if complete:
                    out.append(_res(k, "no", f"{'/'.join(v)} puanın yok", rr))
                else:
                    out.append(_res(k, "check", f"{'/'.join(v)} puanın profilde yok", r))
            elif not known:
                out.append(_res(k, "check", f"{have[0]} puanı girilmemiş", r))
            elif minsc is not None and max(known) < minsc:
                if len(known) < len(have) or (not complete and len(have) < len(v)):
                    out.append(_res(k, "check", f"girilen puan {max(known):g} < {minsc:g}; diğer puan türlerini kontrol et", r))
                else:
                    out.append(_res(k, "no", f"puan {max(known):g} < {minsc:g}", rr))
            else:
                t = max((x for x in have if scores[x] is not None), key=lambda x: scores[x])
                out.append(_res(k, "ok", f"{t} puanın {scores[t]:g}" + (f" (≥{minsc:g})" if minsc else ""), r))
        elif k == "kpss_unknown":
            out.append(_res(k, "check", f"KPSS ({'/'.join(v)}) grubu; puan türü ilanda belirtilmemiş" if v
                            else "KPSS puan türü okunamadı", r))
        elif k == "kpss_min_score" and not has_type:
            out.append(_res(k, "check", f"asgari puan {v:g}: puan türü belirsiz", r))
        elif k == "kpss_year":
            y = _num_or_none(_g(prof, "exams", "kpss", "year"))
            if y is None:
                out.append(_res(k, "check", "KPSS yılı girilmemiş", r))
            else:
                out.append(_res(k, "check" if y < v else "ok", f"KPSS yılın {y:g}, ilan {v} ve sonrası diyor: geçerlilik/şart metnini doğrula", r))
        elif k in ("age_max", "age_min"):
            bd = _g(prof, "personal", "birth_date")
            if not bd:
                out.append(_res(k, "check", "doğum tarihi girilmemiş", r))
                continue
            age = _age(bd, on)
            bad = age >= v if k == "age_max" else age < v
            out.append(_res(k, "no" if bad else "ok", f"yaşın {age} (sınır {'<' if k == 'age_max' else '≥'}{v})", r))
        elif k == "birth_after":
            bd = _g(prof, "personal", "birth_date")
            if not bd:
                out.append(_res(k, "check", "doğum tarihi girilmemiş", r))
                continue
            b, lim = date.fromisoformat(bd), date.fromisoformat(v["date"])
            ok = b >= lim if v.get("inclusive") else b > lim
            out.append(_res(k, "ok" if ok else "no",
                            f"doğum tarihin {bd}; {v['date']} {'ve sonrası' if v.get('inclusive') else 'sonrası'} isteniyor", r))
        elif k == "gender":
            pg = _g(prof, "personal", "gender")
            if pg is None:
                out.append(_res(k, "check", f"cinsiyet şartı: {v}", r))
            else:
                out.append(_res(k, "ok" if pg == v else "no", f"cinsiyet şartı: {v}", r))
        elif k == "education_level":
            edus = _g(prof, "education", default=[]) or []
            done = [edu_rank(e.get("level")) for e in edus if isinstance(e, dict) and e.get("status") in (None, "graduated")]
            stud = [edu_rank(e.get("level")) for e in edus if isinstance(e, dict) and e.get("status") == "student"]
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
            progs = [e.get("program") for e in (_g(prof, "education", default=[]) or [])
                     if isinstance(e, dict) and e.get("program")]
            n = norm(v)
            if any(_overlap(n, p) for p in progs):
                out.append(_res(k, "ok", "bölüm şartı profilinle eşleşiyor", r))
            else:
                out.append(_res(k, "check", "bölüm şartını oku: " + v[:140], r))
        elif k == "certificate_text":
            certs = _g(prof, "credentials", "certificates")
            n = norm(v)
            if certs and any(_overlap(n, c) for c in certs if isinstance(c, str)):
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
            postpone_ok = (v or {}).get("postpone_ok") if isinstance(v, dict) else False
            if g == "K" or m in ("done", "exempt", "not_applicable"):
                out.append(_res(k, "ok", "askerlik şartı karşılanıyor", r))
            elif m == "postponed":
                out.append(_res(k, "ok" if postpone_ok else "no",
                                "askerlik ertelemesi " + ("kabul ediliyor" if postpone_ok else "yeterli değil: yapmış/muaf olmak gerekli"), r))
            elif m is None:
                out.append(_res(k, "check", "askerlik durumu girilmemiş", r))
            elif postpone_ok:
                out.append(_res(k, "check", "askerlik: çağına gelmemiş/ertelenmiş olanlar da başvurabiliyor, durumunu kontrol et", r))
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
                rate = _num_or_none(_g(prof, "social", "disability", "rate"))
                if need and rate is None:
                    out.append(_res(k, "check", f"engel oranı ≥%{need} aranıyor, oranın girilmemiş", r))
                elif need and rate < need:
                    out.append(_res(k, "no", f"engel oranı %{rate:g} < %{need}", r))
                else:
                    out.append(_res(k, "ok", f"{label} şartı karşılanıyor", r))
        elif k == "experience_years":
            ex = _num_or_none(_g(prof, "work", "experience_years"))
            if ex is None:
                out.append(_res(k, "check", "deneyim yılı girilmemiş", r))
            else:
                out.append(_res(k, "ok" if ex >= v else "no", f"deneyim {ex:g} yıl (aranan {v})", r))
        elif k == "language":
            sc = _g(prof, "exams", "language", default={}) or {}
            got = _num_or_none(sc.get(v["exam"]))
            others = [x for x in (_num_or_none(sc.get(e)) for e in ("YDS", "YOKDIL")) if x is not None]
            if got is None and v.get("equiv") and others and max(others) >= v["min"]:
                out.append(_res(k, "check", f"{v['exam']} ≥{v['min']} aranıyor; eşdeğer sınav puanın uygun görünüyor, denkliği doğrula", r))
            elif got is None:
                out.append(_res(k, "check", f"{v['exam']} puanı girilmemiş (aranan {v['min']})", r))
            elif got >= v["min"]:
                out.append(_res(k, "ok", f"{v['exam']} {got:g} (aranan {v['min']})", r))
            elif v.get("equiv"):
                out.append(_res(k, "check", f"{v['exam']} {got:g} < {v['min']}; eşdeğer belge kabul ediliyor, kontrol et", r))
            else:
                out.append(_res(k, "no", f"{v['exam']} {got:g} (aranan {v['min']})", r))
        elif k == "ales":
            sc = _num_or_none(_g(prof, "exams", "ales", "score"))
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
