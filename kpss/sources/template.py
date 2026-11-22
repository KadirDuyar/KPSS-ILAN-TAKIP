"""Kaynak şablonu. Tarayıcı Network sekmesinde bulduğun HERKESE AÇIK uç noktayı doldur."""
URL = "https://ORNEK.gov.tr/api/..."   # TODO: Network sekmesinden
PAYLOAD = {}                           # TODO: Payload sekmesinden


def fetch(session):
    from ..tracker import post_json
    raw = post_json(session, URL, PAYLOAD)
    return [{
        "id": it["id"],                 # TODO: alan adlarını ham cevaba göre eşle
        "title": it["baslik"],
        "institution": it.get("kurum"),
        "type": it.get("tur"),
        "start": it.get("baslangic"),
        "deadline": it.get("bitis"),
        "url": it["link"],
        "text": it.get("metin", ""),
    } for it in raw["items"]]
