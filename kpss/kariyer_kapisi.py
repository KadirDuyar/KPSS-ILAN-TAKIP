#!/usr/bin/env python3
"""Kariyer Kapısı tek ilan test scripti.

Kullanım:
  pip install requests
  python kk_test.py "https://kariyerkapisi.gov.tr/IlanDetay?i=<guid>"
  python kk_test.py <guid> --out ilan.json
  python kk_test.py <guid> --alt                # kadro/kontenjan detayını da çek
  python kk_test.py --file ham.json --alt-file alt.json   # ağ olmadan test

Sadece herkese açık (giriş gerektirmeyen) uç noktayı okur.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime

from . import requirements as R
from .requirements import extract as extract_requirements

# Ayrıştırıcı sürümü: kayıtlı detail_json bundan farklıysa tracker ham cevaplardan ağsız yeniden ayrıştırır.
PARSER_VERSION = 2
API = "https://api.kariyerkapisi.gov.tr/api/ilan/GetIlanPreviewPublic"
DETAIL_URL = "https://kariyerkapisi.gov.tr/IlanDetay?i={guid}"
HEADERS = {
    "User-Agent": "kpss-tracker-test/0.1 (kisisel, dusuk hacimli)",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://kariyerkapisi.gov.tr",
    "Referer": "https://kariyerkapisi.gov.tr/",
}
P_RE = re.compile(r"(?:KPSS\s*\(?\s*|\b)P(\d{1,3})\b")


def p_types(text: str) -> list:
    return sorted({"P" + n for n in P_RE.findall(text)}, key=lambda x: int(x[1:]))


GUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def extract_guid(text: str) -> str:
    m = GUID_RE.search(text)
    if not m:
        raise ValueError("GUID bulunamadı")
    return m.group(0).lower()


def fetch_ilan(guid: str, timeout: int = 20) -> dict:
    import requests  # yalnızca ağ kullanılırken gerekir

    r = requests.post(API, json={"ilanGuid": guid}, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


ALT_API = "https://api.kariyerkapisi.gov.tr/api/altilan/GetAltIlanInfoByIlanIdPublic"


def fetch_alt(guid: str, key: str = "ilanGuid", timeout: int = 20):
    """Payload anahtarı doğrulanmadı: 400 alırsan --alt-key ile Network'teki adı ver."""
    import requests

    r = requests.post(ALT_API, json={key: guid}, headers=HEADERS, timeout=timeout)
    if r.status_code != 200:
        print(f"[alt] HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return None
    return r.json()


def parse_position(p: dict) -> dict:
    """Alt ilan (kadro) kaydı. Sadece metinde açıkça yazanı çıkarır."""
    text = clean_text(p.get("ilanMetni"))

    def g(pat):
        m = re.search(pat, text)
        return m.group(1).strip() if m else None

    scores = p_types(text)
    found = R.consolidate(extract_requirements(text))
    val = lambda k: next((r["value"] for r in found if r["kind"] == k and not r.get("soft")), None)
    min_score, age = val("kpss_min_score"), val("age_max")
    qty = g(r"ADET\s*:\s*(\d+)")
    return {
        "title": " ".join((p.get("ilanBaslik") or "").split()) or None,
        "unvan": " ".join((p.get("unvan") or "").split()) or None,
        "position_no": g(r"İLAN NO\s*:\s*(\S+)"),
        "quantity": int(qty) if qty else None,
        "education": g(r"MEZUNİYET\s*:\s*([^\n]+)"),
        "gender": g(r"CİNSİYET\s*:\s*([^\n]+)"),
        "kpss_score_type": scores[0] if scores else None,
        "kpss_score_types": scores or None,
        "min_score": min_score,
        "max_age": age,
        "provinces": [
            {"il": k.get("il"), "kontenjan": k.get("kontenjan")}
            for k in (p.get("kontenjanList") or [])
        ],
        "stages": [
            {"name": a.get("asamaAdi"), "type": a.get("turu"), "weight": a.get("agirlik")}
            for a in (p.get("degerlemeAsamaList") or [])
        ],
        "requirements_text": text,
        "requirements": extract_requirements(text),
    }


def parse_positions(raw) -> list:
    if isinstance(raw, dict):  # zarf olabilir
        raw = next((v for v in raw.values() if isinstance(v, list)), [raw])
    return [parse_position(p) for p in raw]


def unwrap(raw: dict) -> dict:
    """Cevap bir zarfın içindeyse aç."""
    if "ilanBaslik" in raw:
        return raw
    for key in ("data", "Data", "result", "value"):
        if isinstance(raw.get(key), dict) and "ilanBaslik" in raw[key]:
            return raw[key]
    return raw


def clean_text(t: str) -> str:
    t = re.sub(r"\[/?[a-zA-Z]+(?:=[^\]]*)?\]", "", t or "")  # [justify], [size=14pt]
    t = t.replace("\u00a0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def parse_dt(s):
    try:
        return datetime.fromisoformat(s).isoformat() if s else None
    except ValueError:
        return None


def find_kpss(text: str) -> dict:
    """Sadece metinde AÇIKÇA yazanı çıkarır; tahmin yok."""
    groups = sorted(set(g.upper() for g in re.findall(r"KPSS\s*\(([A-Za-z])\)\s*grubu", text, re.I)))
    types = p_types(text)
    return {"kpss_group": groups or None, "kpss_score_types": types or None}


def normalize(raw: dict, guid: str) -> dict:
    raw = unwrap(raw)
    text = clean_text(raw.get("ilanMetni"))
    kpss = find_kpss(text)
    item = {
        "parser": PARSER_VERSION,
        "source": "kariyer_kapisi",
        "source_id": raw.get("ilN_NO") or guid,
        "guid": guid,
        "institution": raw.get("kurumAdi"),
        "unit": raw.get("birimAdi"),
        "title": raw.get("ilanBaslik"),
        "employment_type": (raw.get("ilanTuru") or "").strip(", ") or None,
        "application_start": parse_dt(raw.get("basTarih")),
        "application_deadline": parse_dt(raw.get("bitTarih")),
        "quota": raw.get("kontenjan") or None,  # 0 ise kaynakta yok sayılır
        "detail_url": DETAIL_URL.format(guid=guid),
        "edevlet_url": raw.get("eDevletServisURL") or None,
        "requirements_text": text,
        "requirements": extract_requirements(text),
        **kpss,
    }
    stable = "|".join(str(item[k]) for k in ("title", "application_deadline", "requirements_text"))
    item["content_hash"] = hashlib.sha256(stable.encode()).hexdigest()
    # Eksik alanları işaretle (uydurma yok)
    item["needs_review"] = [k for k in ("institution", "title", "application_deadline") if not item[k]]
    if not item["kpss_group"] and not item["kpss_score_types"]:
        item["needs_review"].append("kpss_not_found_in_text")
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="ilan URL'si veya GUID")
    ap.add_argument("--file", help="kayıtlı ham JSON (ağ kullanmaz)")
    ap.add_argument("--out", help="normalize çıktıyı bu dosyaya yaz")
    ap.add_argument("--raw", help="ham cevabı bu dosyaya kaydet")
    ap.add_argument("--alt", action="store_true", help="kadro/kontenjan detayını da çek")
    ap.add_argument("--alt-key", default="ilanGuid", help="alt ilan isteğindeki payload anahtarı")
    ap.add_argument("--alt-file", help="kayıtlı alt ilan JSON'u (ağ kullanmaz)")
    a = ap.parse_args()

    if a.file:
        with open(a.file, encoding="utf-8") as f:
            raw = json.load(f)
        guid = extract_guid(a.target) if a.target else "00000000-0000-0000-0000-000000000000"
    elif a.target:
        guid = extract_guid(a.target)
        raw = fetch_ilan(guid)
    else:
        ap.error("URL/GUID veya --file ver")

    if a.raw:
        with open(a.raw, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    item = normalize(raw, guid)

    alt_raw = None
    if a.alt_file:
        with open(a.alt_file, encoding="utf-8") as f:
            alt_raw = json.load(f)
    elif a.alt:
        alt_raw = fetch_alt(guid, a.alt_key)
    if alt_raw:
        item["positions"] = parse_positions(alt_raw)
        types = {t for p in item["positions"] for t in (p.get("kpss_score_types") or [])}
        if types:
            item["kpss_score_types"] = sorted(types, key=lambda x: int(x[1:]))
            item["needs_review"] = [x for x in item["needs_review"] if x != "kpss_not_found_in_text"]
    preview = {**item, "requirements_text": item["requirements_text"][:150] + "..."}
    for p in preview.get("positions", []):
        p["requirements_text"] = p["requirements_text"][:80] + "..."
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
