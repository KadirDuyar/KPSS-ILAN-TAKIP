"""Supabase/PostgreSQL: profiles tablosundaki her kullanıcı için eşleşmeleri hesaplar -> matches.

  python -m kpss.sync [--db postgresql://...] [--today YYYY-MM-DD]

Akış: aktif ilanlar x profiller -> matching.evaluate -> matches (upsert) + eski satırları sil
      + yeni/yükselen eşleşmeler için notification_outbox'a NEW_MATCH olayı yaz.
Profil verisi kullanıcıdan gelir (güvenilmez): yalnızca beklenen alanlar okunur, hata profili atlatır.
"""
import argparse
import json
import sys
from datetime import datetime

from . import db as dbm
from . import matching as m
from . import requirements as reqs

RANK = {"ok": 0, "check": 1, "no": 2}


def _positions(ev):
    """Gösterim için: en iyi düzeydeki kadrolar (en çok 4) + her birinin gerekçeleri."""
    out = []
    for level, p, reasons in ev["cands"]:
        if level != ev["level"] or not p:
            continue
        out.append({"unvan": p.get("unvan"), "types": p.get("kpss_score_types") or [], "min": p.get("min_score"),
                    "il": ", ".join(f"{x['il']} ({x['kontenjan']})" for x in (p.get("provinces") or [])[:2]),
                    "reasons": reasons[:5]})
    return out[:4]


def sync(con, now=None) -> dict:
    now = now or datetime.now()
    stats = {"profiles": 0, "matches": 0, "new": 0, "removed": 0, "skipped": 0}
    rows = con.execute("SELECT * FROM ilan WHERE active=1 ORDER BY application_deadline").fetchall()
    for pr in con.execute("SELECT user_id, email, data FROM profiles").fetchall():
        uid = str(pr["user_id"])
        try:
            data = json.loads(pr["data"]) if isinstance(pr["data"], str) else (pr["data"] or {})
            prof = reqs.normalize_profile({**data, "name": pr["email"] or uid})
        except Exception as e:  # bozuk profil diğer kullanıcıları etkilemesin
            print(f"[sync] profil atlandı ({uid}): {e}", file=sys.stderr)
            stats["skipped"] += 1
            continue
        stats["profiles"] += 1
        notify_check = m.pref(prof, "notify_check", True)
        prev = {r["guid"]: r for r in con.execute("SELECT guid, level, fp FROM matches WHERE user_id=?", (uid,))}
        keep = set()
        for row in rows:
            try:
                ev = m.evaluate(row, prof, now)
            except Exception as e:  # ilan kaybolmasın: elle kontrol gereken ilan olarak göster
                print(f"[sync] {row['guid']} değerlendirilemedi: {e}", file=sys.stderr)
                stats["errors"] = stats.get("errors", 0) + 1
                ev = {"level": "check", "cands": [("check", None, ["otomatik değerlendirme başarısız, ilanı elle kontrol et"])]}
            if not ev:
                continue
            reasons = sorted({r for c in ev["cands"] if c[0] == ev["level"] for r in c[2]})[:8]
            con.execute(
                """INSERT INTO matches (user_id, guid, level, reasons, positions, fp, computed_at)
                   VALUES (?, ?, ?, cast(? as jsonb), cast(? as jsonb), ?, now())
                   ON CONFLICT (user_id, guid) DO UPDATE SET level=excluded.level, reasons=excluded.reasons,
                     positions=excluded.positions, fp=excluded.fp, computed_at=now()""",
                (uid, row["guid"], ev["level"], json.dumps(reasons, ensure_ascii=False),
                 json.dumps(_positions(ev), ensure_ascii=False), row["list_fp"]))
            keep.add(row["guid"])
            stats["matches"] += 1
            old = prev.get(row["guid"])
            worthy = ev["level"] == "ok" or (ev["level"] == "check" and notify_check)
            if worthy and (old is None or RANK[ev["level"]] < RANK[old["level"]]):
                con.execute(
                    """INSERT INTO notification_outbox (user_id, guid, event, payload)
                       VALUES (?, ?, 'NEW_MATCH', cast(? as jsonb)) ON CONFLICT DO NOTHING""",
                    (uid, row["guid"], json.dumps({"level": ev["level"], "fp": row["list_fp"], "title": row["title"]},
                                                  ensure_ascii=False)))
                stats["new"] += 1
        for guid in set(prev) - keep:
            con.execute("DELETE FROM matches WHERE user_id=? AND guid=?", (uid, guid))
            stats["removed"] += 1
        con.commit()
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--today", help="YYYY-MM-DD (test)")
    a = ap.parse_args(argv)
    con = dbm.connect(a.db)
    if not con.pg:
        print("sync yalnızca PostgreSQL/Supabase için (DATABASE_URL=postgresql://...).", file=sys.stderr)
        return 2
    con.ensure_schema()
    now = datetime.fromisoformat(a.today + "T00:00:00") if a.today else datetime.now()
    print("[sync]", sync(con, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
