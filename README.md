# KPSS İlan Takip

Kariyer Kapısı'nın **herkese açık** API'sinden ilanları çeker, her ilanın hangi adresten alındığını saklar, her kullanıcının profiline göre **uygun / kontrol et / uygun değil** kararı üretir ve bir web sitesinde gösterir.

```
GitHub Actions (30 dk)  ──►  Supabase Postgres  ◄──  Vercel (statik site, supabase-js)
 tara + eşleştir              ilan, profiles,          giriş, profil formu,
 (python -m kpss)             matches, outbox          eşleşmeler (RLS ile kişiye özel)
```
Aynı Python kodu SQLite (yerel), yerel Supabase (Docker) ve bulut Supabase ile çalışır; yalnızca `DATABASE_URL` değişir.

## Supabase + Vercel kurulumu (bir kez, ~15 dk)
1. **Supabase** → New project (ücretsiz). Parolayı not al.
2. **Şema:** SQL Editor'e `supabase/migrations/0001_init.sql`, sonra `0002_users_matches.sql` dosyasının içeriğini yapıştırıp çalıştır. (CLI tercih edersen: `supabase link --project-ref <ref>` → `supabase db push`.)
3. **Kullanıcılar:** Authentication → Sign In / Providers → Email'de *Confirm email*'i kapat, *Allow new users to sign up*'ı kapat. Authentication → Users → *Add user* ile 3-4 kişiyi e-posta + şifre ile ekle.
4. **GitHub** → repo oluştur, bu klasörü it. Settings → Secrets and variables → Actions → `DATABASE_URL` = Supabase → Connect → **Session pooler** bağlantı adresi (IPv4 çalışır; `[YOUR-PASSWORD]` yerine parolayı yaz).
5. Actions → `scan` → *Run workflow*. İlk çalışma ilanları çekip tabloları doldurur.
6. `web/config.js` içine Project Settings → API'den `SUPABASE_URL` ve anon/publishable anahtarı yaz, commit et. (**service_role anahtarını asla yazma.**)
7. **Vercel** → Add New → Project → repoyu seç. Framework: Other. `vercel.json` çıktı klasörünü (`web`) zaten belirtiyor, build komutu yok. Deploy.
8. Siteye gir → Profilim → doldur → Kaydet. Eşleşmeler bir sonraki taramada (en geç ≈30 dk) görünür; hemen görmek için Actions'ta *Run workflow*.

## Güvenlik modeli
- `ilan`, `runs`, `notified`, `notification_outbox` tabloları RLS ile kapalı; tarayıcı yalnızca `ilan_public` (ham veri yok) ve kendi `my_matches` / `profiles` / `matches` satırlarını okuyabilir.
- Tarama işi `DATABASE_URL` ile bağlanır (RLS'yi aşar); bu adres yalnızca GitHub Secrets'ta durur.
- Engellilik/hükümlülük alanları `consent.special_categories` açık değilse hiç değerlendirilmez.

## Yerel çalışma
**SQLite (Docker'sız):**
```
pip install -r requirements.txt
python -m kpss                       # tara -> web/data.json
python -m http.server -d web 8000    # http://localhost:8000/local.html  (profil: profiles.example.json)
```
**Yerel Supabase (Docker):** `supabase init && supabase start && supabase db reset`, sonra
`export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres` ve `python -m kpss`.
Yalnızca eşleştirmeyi yeniden hesapla: `python -m kpss --no-scan`.

## Profil ve şartlar
`profiles.example.json` (v2) alanları: `personal`, `education[]`, `exams`, `student`, `work`, `credentials`, `social`, `consent`, `preferences`. `null` = bilinmiyor → ilgili şartta "kontrol et"; metinden okunamayan şart asla "uygun değil" sayılmaz. Şart çıkarıcı: `kpss/requirements.py`; testler: `python -m unittest discover -s tests -v`.

## Her ilanın kaynağı
`ilan.url` (kullanıcıya verilen resmî sayfa) ve `ilan.source_refs` (makine adresleri, payload, çekilme zamanı) saklanır; arayüzde "Kaynak" altında görünür.

## Yeni kaynak eklemek
`kpss/sources/template.py` → kopyala, uç noktayı doldur, `sources/__init__.py` REGISTRY'ye ekle. Yalnızca girişsiz erişilen sayfalar; robots.txt ve kullanım koşullarına uy, seyrek tara, korumaları aşmaya çalışma. `tools/probe.py` bir adresin ne döndürdüğünü ölçer. ilan.gov.tr için eksik ara sertifika: `REQUESTS_CA_BUNDLE=certs/ilan-bundle.pem` (`verify=False` kullanma).

## Ayrıştırmayı yenileme (ağsız)
`python -m kpss.tracker --reparse`
