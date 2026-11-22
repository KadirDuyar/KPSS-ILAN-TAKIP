#!/usr/bin/env python3
"""Erişim tanısı: her siteye BİR istek atar, hangi katmanda takıldığını söyler.

  python tools/reach_test.py            # tablo yazar; GITHUB_STEP_SUMMARY varsa orada da gösterir

Katmanlar: DNS -> TCP:443 -> TLS+HTTP. Dürüst bir User-Agent kullanır; tarayıcı taklidi, Referer/cookie
sahteciliği, doğrulamayı kapatma YOK. Amaç engeli aşmak değil, nerede olduğunu görmek.
Yorum: zaman aşımı = ağ düzeyinde engel (IP/ülke/veri merkezi); 403/429/503 = uygulama (WAF/hız) engeli;
404 = yol yok (engel olmayabilir); TLS hatası = eksik ara sertifika olabilir.
"""
import json
import os
import socket
import sys
import time
from urllib.parse import urlparse

import requests

UA = "kpss-tracker-reachability-test/0.1 (kisisel; tek istek)"
KK_BODY = {"krM_ID": 0, "searchText": "", "il": "0", "ilanTuru": "0"}

# (ad, yöntem, adres, gövde)
TARGETS = [
    ("KONTROL  example.com", "GET", "https://example.com/", None),
    ("KONTROL  Supabase (pooler DNS)", "GET", "https://supabase.com/", None),
    ("Kariyer Kapısı  API (liste)", "POST", "https://api.kariyerkapisi.gov.tr/api/ilan/GetIseAlimPage", KK_BODY),
    ("Kariyer Kapısı  site", "GET", "https://kariyerkapisi.gov.tr/", None),
    ("ilan.gov.tr  robots.txt", "GET", "https://www.ilan.gov.tr/robots.txt", None),
    ("ilan.gov.tr  ana sayfa", "GET", "https://www.ilan.gov.tr/", None),
    ("kamuilan.sbb.gov.tr  ana sayfa", "GET", "https://kamuilan.sbb.gov.tr/", None),
    ("iskur.gov.tr  ana sayfa", "GET", "https://www.iskur.gov.tr/", None),
    ("İŞKUR e-Şube", "GET", "https://esube.iskur.gov.tr/", None),
    ("Resmî Gazete  ana sayfa", "GET", "https://www.resmigazete.gov.tr/", None),
]


def egress() -> str:
    try:
        d = requests.get("https://ipinfo.io/json", timeout=10, headers={"User-Agent": UA}).json()
        return f"{d.get('ip')} · {d.get('country')} · {d.get('org')}"
    except Exception as e:
        return f"alınamadı ({type(e).__name__})"


def probe(name, method, url, body) -> dict:
    host = urlparse(url).hostname
    r = {"ad": name, "dns": "-", "tcp": "-", "http": "-", "sonuc": "", "ms": ""}
    t0 = time.time()
    try:
        socket.getaddrinfo(host, 443)
        r["dns"] = "ok"
    except Exception as e:
        r.update(dns="HATA", sonuc=f"DNS çözülemedi ({type(e).__name__})")
        return r
    try:
        s = socket.create_connection((host, 443), timeout=8)
        s.close()
        r["tcp"] = "ok"
    except Exception as e:
        r.update(tcp="ZAMAN AŞIMI" if isinstance(e, socket.timeout) or "timed out" in str(e) else "HATA",
                 sonuc="ağ düzeyinde engel olası (TCP kurulamadı)")
        r["ms"] = f"{(time.time() - t0) * 1000:.0f}"
        return r
    try:
        resp = requests.request(method, url, json=body, timeout=15, headers={"User-Agent": UA}, allow_redirects=True)
        r["ms"] = f"{(time.time() - t0) * 1000:.0f}"
        c = resp.status_code
        r["http"] = str(c)
        ctype = resp.headers.get("Content-Type", "").split(";")[0]
        srv = resp.headers.get("Server", "")
        extra = f"{ctype or '?'} · {len(resp.content)} bayt" + (f" · server={srv}" if srv else "")
        if 200 <= c < 400:
            ok = "veri JSON" if "json" in ctype else ""
            r["sonuc"] = f"✅ erişilebilir ({extra}) {ok}".strip()
        elif c in (401, 403, 429, 503):
            r["sonuc"] = f"⛔ uygulama düzeyi engel/hız sınırı ({extra})"
        elif c == 404:
            r["sonuc"] = f"❔ yol yok ({extra}) – engel olmayabilir"
        else:
            r["sonuc"] = f"⚠️ beklenmeyen ({extra})"
    except requests.exceptions.SSLError as e:
        r["http"] = "TLS"
        r["sonuc"] = "🔒 TLS doğrulanamadı (eksik ara sertifika olabilir; doğrulama kapatılmaz)"
    except requests.exceptions.Timeout:
        r["http"] = "ZAMAN AŞIMI"
        r["sonuc"] = "⛔ TCP kuruldu ama HTTP cevap vermedi (sessiz düşürme olası)"
        r["ms"] = f"{(time.time() - t0) * 1000:.0f}"
    except Exception as e:
        r["http"] = type(e).__name__
        r["sonuc"] = f"⚠️ {str(e)[:100]}"
    return r


def main() -> int:
    where = os.environ.get("REACH_LABEL") or ("GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "yerel")
    ip = egress()
    rows = []
    for t in TARGETS:
        rows.append(probe(*t))
        time.sleep(1)  # nazik ol
    lines = [f"### Erişim testi — {where}", f"Çıkış IP: `{ip}`", "",
             "| Site | DNS | TCP | HTTP | ms | Sonuç |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['ad']} | {r['dns']} | {r['tcp']} | {r['http']} | {r['ms']} | {r['sonuc']} |" for r in rows]
    out = "\n".join(lines)
    print(out)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    json.dump({"where": where, "ip": ip, "rows": rows}, open("reach_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
