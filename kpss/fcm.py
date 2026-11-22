"""Firebase Cloud Messaging HTTP v1 göndericisi (ücretsiz).

Yapılandırma (yalnızca sunucuda, repoda ASLA):
  FCM_SERVICE_ACCOUNT=/home/<kullanıcı>/.kpss-fcm.json   # Firebase -> Proje ayarları -> Hizmet hesapları
                                                          # -> "Yeni özel anahtar oluştur"; chmod 600
Yapılandırma yoksa configured() False döner ve bildirim adımı hiçbir olaya dokunmadan atlanır
(ör. GitHub Actions'ta anahtar yoksa gönderimi VM yapar).
"""
import json
import os

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
ENV = "FCM_SERVICE_ACCOUNT"
ANDROID_CHANNEL = "ilanlar"          # mobil uygulama bu kimlikle bir bildirim kanalı oluşturmalı


class TokenGone(Exception):
    """Token artık geçersiz (uygulama kaldırıldı / token yenilendi): kalıcı olarak kapatılmalı."""


class TemporaryError(Exception):
    """Geçici hata (ağ, kota, FCM arızası): olay geri bırakılır, sonra tekrar denenir."""


def configured() -> bool:
    p = os.environ.get(ENV)
    return bool(p) and os.path.isfile(os.path.expanduser(p))


class Sender:
    def __init__(self, path=None, session=None):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        path = os.path.expanduser(path or os.environ[ENV])
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        self.project = info["project_id"]
        creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        self.http = session or AuthorizedSession(creds)
        self.url = f"https://fcm.googleapis.com/v1/projects/{self.project}/messages:send"

    def send(self, token: str, title: str, body: str, data: dict = None, click_url: str = None):
        msg = {
            "token": token,
            "notification": {"title": title[:120], "body": body[:900]},
            # data değerleri FCM'de yalnızca metin olabilir; uygulama bildirime dokununca url'yi açar.
            "data": {k: str(v) for k, v in {**(data or {}), "url": click_url or ""}.items() if v is not None},
            "android": {"priority": "high", "notification": {"channel_id": ANDROID_CHANNEL}},
            "apns": {"payload": {"aps": {"sound": "default"}}},
        }
        try:
            r = self.http.post(self.url, json={"message": msg}, timeout=15)
        except Exception as e:
            raise TemporaryError(repr(e))
        if r.status_code == 200:
            return
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        codes = {d.get("errorCode") for d in err.get("details", []) if isinstance(d, dict)}
        text = f"FCM HTTP {r.status_code} {err.get('status', '')} {','.join(sorted(c for c in codes if c))}".strip()
        if r.status_code == 404 or codes & {"UNREGISTERED", "SENDER_ID_MISMATCH"} or (
                r.status_code == 400 and "INVALID_ARGUMENT" in codes | {err.get("status")} and "token" in str(err.get("message", "")).lower()):
            raise TokenGone(text)
        if r.status_code in (401, 403):
            raise RuntimeError(text + " (servis hesabı/izin sorunu)")   # yapılandırma hatası: çalışmayı durdur
        raise TemporaryError(text)
