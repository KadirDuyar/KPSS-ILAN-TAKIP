# Bildirimler (FCM)

Akış: `sync` yeni/yükselen eşleşmeyi ve tracker metni değişen ilanı `notification_outbox`'a yazar →
`python -m kpss` her çalışmada `kpss/notify.py` ile bunları kullanıcının telefonlarına FCM HTTP v1 üzerinden gönderir.
Ücretsizdir (FCM'de mesaj sınırı yok).

## 1. Firebase projesi (bir kez)

1. <https://console.firebase.google.com> → **Proje ekle** (Google Analytics gerekmez).
2. **Android uygulaması ekle** → paket adı mobil uygulamadaki `app.json` → `android.package` ile aynı olmalı.
   İndirilen `google-services.json` mobil uygulamanın kök klasörüne konur ve `app.json`'a
   `"android": { "googleServicesFile": "./google-services.json" }` eklenir. Bu dosya gizli değildir ama repoya
   koymak yerine yerelde tutmak daha düzenlidir.
3. **Proje ayarları → Hizmet hesapları → Yeni özel anahtar oluştur** → indirilen JSON **gizlidir** (sunucu anahtarı).

## 2. Sunucu (VM / Raspberry Pi)

```bash
# anahtarı Windows'tan VM'e kopyala (scp vb.), sonra:
mv ~/indirilen-anahtar.json ~/.kpss-fcm.json && chmod 600 ~/.kpss-fcm.json
echo 'FCM_SERVICE_ACCOUNT=/home/'"$USER"'/.kpss-fcm.json' >> ~/.kpss.env
cd ~/kpss && .venv/bin/pip install -r requirements.txt     # google-auth
set -a; . ~/.kpss.env; set +a; .venv/bin/python -m kpss.notify --dry-run
```

- Anahtar **asla** repoya, web'e ya da mobil uygulamaya girmez. GitHub Actions'a eklenmesi gerekmez:
  orada anahtar yoksa bildirim adımı hiçbir olaya dokunmadan atlanır, gönderimi VM yapar.
- Logda `[notify] {'sent': …, 'no_device': …, 'tokens_disabled': …}` görünür.
  `no_device`: kullanıcının kayıtlı telefonu yok (olay bekler, telefon eklenince son 7 günün olayları gelir).

## 3. Mobil uygulama (Expo)

FCM doğrudan kullanıldığı için uygulama **kendi derlemesinde** çalışmalı (APK / EAS / `expo run:android`);
Expo Go'da yerel FCM token alınamaz.

```ts
import * as Notifications from "expo-notifications";
import { Platform, Linking } from "react-native";
import { supabase } from "./lib/supabase";

export async function registerForPush() {
  if (Platform.OS === "android") {
    // Sunucu bildirimleri bu kanal kimliğiyle gönderir (kpss/fcm.py: ANDROID_CHANNEL)
    await Notifications.setNotificationChannelAsync("ilanlar", {
      name: "İlanlar", importance: Notifications.AndroidImportance.HIGH,
    });
  }
  const { status } = await Notifications.requestPermissionsAsync();
  if (status !== "granted") return;
  const { data: token } = await Notifications.getDevicePushTokenAsync();   // yerel FCM token
  await supabase.rpc("register_push_token", { p_token: token, p_platform: Platform.OS });
}

// Oturum kapatılırken: await supabase.rpc("unregister_push_token", { p_token: token });

// Bildirime dokununca resmî ilan sayfasını aç
Notifications.addNotificationResponseReceivedListener(r => {
  const url = r.notification.request.content.data?.url;
  if (typeof url === "string" && url.startsWith("https://")) Linking.openURL(url);
});
```

`registerForPush()` her oturum açılışında çağrılabilir: token aynıysa yalnızca `last_seen_at` yenilenir.

## Veritabanı

`supabase/migrations/0003_push_tokens.sql`:
- `push_tokens`: kullanıcı yalnızca kendi token'larını okuyabilir; doğrudan yazamaz.
- `register_push_token(token, platform)`: token'ı oturumdaki kullanıcıya bağlar (cihaz başka hesaba geçtiyse taşır),
  kullanıcı başına en çok 10 etkin cihaz.
- `unregister_push_token(token)`: yalnızca kendi token'ını siler.
- FCM token'ın geçersiz olduğunu söylerse (`UNREGISTERED`) sunucu `disabled_at` ile kapatır.

Migration, `python -m kpss` ilk çalıştığında `push_tokens` tablosu yoksa otomatik uygulanır
(Supabase SQL Editor'da elle de çalıştırılabilir; tekrar çalıştırmak güvenlidir).
