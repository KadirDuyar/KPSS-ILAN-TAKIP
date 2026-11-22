"""python -m kpss [--db HEDEF] [--no-scan] [--export]

Hedef PostgreSQL/Supabase ise:  tara -> kullanıcı eşleşmelerini hesapla (matches tablosu).
Hedef SQLite ise:               tara -> web/data.json (yerel önizleme için, web/local.html).
"""
import argparse

from . import db as dbm
from . import export, sync, tracker

ap = argparse.ArgumentParser()
ap.add_argument("--db", help="SQLite yolu veya postgresql:// adresi (varsayılan: $DATABASE_URL ya da data/kpss.db)")
ap.add_argument("--no-scan", action="store_true", help="kaynakları çekme, yalnızca eşleştir/dışa aktar")
ap.add_argument("--export", action="store_true", help="PostgreSQL'de de web/data.json üret")
a = ap.parse_args()

if not a.no_scan:
    tracker.run(argparse.Namespace(db=a.db, list_file=None, no_detail=False, reparse=False))
con = dbm.connect(a.db)
con.ensure_schema()
if con.pg:
    print("[sync]", sync.sync(con))
if a.export or not con.pg:
    export.main(db=a.db)
