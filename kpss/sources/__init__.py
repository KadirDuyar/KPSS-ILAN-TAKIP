"""Ek kaynaklar. Her kaynak bir fonksiyon: fetch(session) -> [kayıt, ...]
kayıt = {id, title, institution, type, start, deadline, url, text}  (start/deadline ISO ya da None)
Yeni kaynak: sources/<ad>.py yaz (template.py'ye bak), buraya ekle."""
REGISTRY = {}  # örn: from . import ilan_gov; REGISTRY["ilan_gov"] = ilan_gov.fetch
