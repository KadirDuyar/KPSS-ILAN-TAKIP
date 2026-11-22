#!/usr/bin/env python3
"""Bir kaynağın ne döndürdüğünü ölçer: durum, içerik türü, boyut; PDF ise metin katmanı var mı?

  pip install requests pymupdf
  python probe.py "https://kamuilan.sbb.gov.tr/ilanDetay.aspx?kod=..."
  python probe.py ilan.pdf                      # yerel dosya
  python probe.py "https://www.ilan.gov.tr/sitemap/daily-ads.xml"

Tek istek atar, hiçbir korumayı aşmaya çalışmaz: 403/engel gelirse bunu raporlar ve durur.
"""
import hashlib
import os
import sys



def pdf_report(data: bytes):
    try:
        import pymupdf  # PyMuPDF
    except ImportError:
        print("PDF analizi için: pip install pymupdf")
        return
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = [p.get_text() for p in doc]
    chars = sum(len(t.strip()) for t in pages)
    images = sum(len(p.get_images()) for p in doc)
    print(f"sayfa: {len(pages)} | metin karakteri: {chars} | gömülü görsel: {images}")
    if chars < 50 * max(1, len(pages)):
        print("=> METİN KATMANI YOK/ZAYIF: taranmış PDF olabilir, OCR gerekir")
    else:
        print("=> metin katmanı VAR: doğrudan ayrıştırılabilir")
    print("--- ilk sayfadan ilk 600 karakter ---")
    print((pages[0] if pages else "")[:600])
    kpss = [i + 1 for i, t in enumerate(pages) if "KPSS" in t]
    print(f"--- 'KPSS' geçen sayfalar: {kpss[:10]}")


def main(target: str):
    if os.path.exists(target):
        data, ctype = open(target, "rb").read(), "application/pdf"
        print(f"yerel dosya: {len(data)} bayt")
    else:
        import requests

        r = requests.get(target, timeout=30, headers={"User-Agent": "kpss-tracker-probe/0.1 (kisisel, tek istek)"})
        ctype = r.headers.get("Content-Type", "")
        data = r.content
        print(f"HTTP {r.status_code} | {ctype} | {len(data)} bayt | "
              f"Content-Disposition: {r.headers.get('Content-Disposition')} | sha256: {hashlib.sha256(data).hexdigest()[:16]}")
        if r.status_code != 200:
            print("=> engellendi/hata: bu kaynağı otomatik taramayalım, yalnızca bağlantı olarak kullanalım")
            return
    if data[:5] == b"%PDF-" or "pdf" in ctype.lower():
        pdf_report(data)
    else:
        print("PDF değil. İlk 300 karakter:\n", data[:300].decode("utf-8", "replace"))


if __name__ == "__main__":
    main(sys.argv[1]) if len(sys.argv) > 1 else print(__doc__)
