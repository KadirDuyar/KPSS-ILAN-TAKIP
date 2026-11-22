"""İnce veritabanı katmanı: aynı kod SQLite (hızlı yerel test) ve PostgreSQL (yerel Supabase, bulut) ile çalışır.

connect(hedef): 'postgresql://...' ise PostgreSQL, değilse SQLite dosya yolu.
Hedef verilmezse DATABASE_URL ortam değişkeni, o da yoksa data/kpss.db kullanılır.
SQL '?' ve ':ad' yer tutucularıyla yazılır; PostgreSQL için otomatik çevrilir.
Satırlar row["kolon"] ile okunur; tarih ve JSON alanları SQLite'taki gibi metin döner (eski kod değişmesin).
"""
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

MIGRATIONS = sorted((Path(__file__).resolve().parent.parent / "supabase" / "migrations").glob("*.sql"))

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ilan (
  guid TEXT PRIMARY KEY, source TEXT DEFAULT 'kariyer_kapisi', url TEXT, source_refs TEXT,
  institution TEXT, unit TEXT, title TEXT, ilan_type TEXT,
  application_start TEXT, application_deadline TEXT, external_url TEXT,
  list_fp TEXT, content_hash TEXT, detail_json TEXT, raw_json TEXT,
  active INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS runs (ts TEXT, listed INTEGER, new INTEGER, changed INTEGER, errors INTEGER);
CREATE TABLE IF NOT EXISTS notified (profile TEXT, guid TEXT, fp TEXT, ts TEXT, PRIMARY KEY (profile, guid, fp));
"""
SQLITE_NEW_COLUMNS = (("raw_json", "TEXT"), ("source", "TEXT DEFAULT 'kariyer_kapisi'"),
                      ("url", "TEXT"), ("source_refs", "TEXT"))


def _norm(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _pg_rows(cursor):
    cols = [d.name for d in (cursor.description or [])]
    return lambda values: {c: _norm(v) for c, v in zip(cols, values)}


def _pg_sql(sql: str) -> str:
    sql = sql.replace("%", "%%")
    sql = re.sub(r"(?<![:\w]):(\w+)", r"%(\1)s", sql)
    return sql.replace("?", "%s")


class DB:
    def __init__(self, raw, pg: bool):
        self.raw, self.pg = raw, pg

    def execute(self, sql, params=()):
        return self.raw.execute(_pg_sql(sql) if self.pg else sql, params)

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()

    def table_exists(self, name: str) -> bool:
        if self.pg:
            q = "select 1 from information_schema.tables where table_schema='public' and table_name=%s"
        else:
            q = "select 1 from sqlite_master where type='table' and name=?"
        return self.raw.execute(q, (name,)).fetchone() is not None

    def ensure_schema(self):
        """Eksik tabloları/kolonları tamamlar (idempotent)."""
        if self.pg:
            # Yeni migration eklendiğinde buraya o migration'ın tablosunu ekle: eksikse hepsi (idempotent) çalışır.
            if not all(self.table_exists(t) for t in ("ilan", "profiles", "matches", "push_tokens")):
                for f in MIGRATIONS:  # hepsi idempotent; Supabase CLI kullanıyorsan zaten uygulanmıştır
                    try:
                        self.raw.execute(f.read_text(encoding="utf-8"))
                        self.raw.commit()
                    except Exception as e:  # bir migration hatası taramayı durdurmasın; logda görünür
                        self.raw.rollback()
                        print(f"[db] migration {f.name} uygulanamadı: {str(e).splitlines()[0]}", file=sys.stderr)
        else:
            self.raw.executescript(SQLITE_SCHEMA)
            have = {r[1] for r in self.raw.execute("PRAGMA table_info(ilan)")}
            for col, ddl in SQLITE_NEW_COLUMNS:
                if col not in have:
                    self.raw.execute(f"ALTER TABLE ilan ADD COLUMN {col} {ddl}")
        self.raw.commit()


def _pg_label(target: str) -> str:
    u = urlparse(target)  # parola gösterilmez
    return f"{u.hostname or 'yerel soket'}:{u.port or ''}{u.path}"


def connect(target=None) -> DB:
    target = target or os.environ.get("DATABASE_URL") or "data/kpss.db"
    if re.match(r"postgres(ql)?://", target):
        try:
            import psycopg
        except ImportError:
            raise SystemExit("PostgreSQL için psycopg gerekli: pip install 'psycopg[binary]'\n"
                             "SQLite kullanacaksan DATABASE_URL'i temizle: unset DATABASE_URL")

        print(f"[db] PostgreSQL {_pg_label(target)}", file=sys.stderr)
        try:
            return DB(psycopg.connect(target, row_factory=_pg_rows), pg=True)
        except psycopg.OperationalError as e:
            raise SystemExit(
                f"PostgreSQL'e bağlanılamadı ({_pg_label(target)}).\n"
                "  Docker'daki Supabase çalışıyor mu? (supabase start)\n"
                "  SQLite kullanacaksan: unset DATABASE_URL\n"
                f"  Ayrıntı: {str(e).splitlines()[0]}")
    print(f"[db] SQLite {target}", file=sys.stderr)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    raw = sqlite3.connect(target)
    raw.row_factory = sqlite3.Row
    return DB(raw, pg=False)
