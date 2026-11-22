"""DB + profiller -> web/data.json (HTML arayüzü bunu okur)."""
import json
import os
from datetime import datetime

from . import db as dbm
from . import matching as m
from . import requirements as reqs


def main(db=None, profiles="profiles.json", out="web/data.json", now=None):
    now = now or datetime.now()
    if not os.path.exists(profiles):
        profiles = "profiles.example.json"
    profs = [reqs.normalize_profile(x) for x in json.load(open(profiles, encoding="utf-8"))["profiles"]]
    con = dbm.connect(db)
    con.ensure_schema()
    items = []
    for row in con.execute("SELECT * FROM ilan WHERE active=1 ORDER BY application_deadline"):
        dl = row["application_deadline"]
        if dl and datetime.fromisoformat(dl) < now:
            continue
        d = json.loads(row["detail_json"]) if row["detail_json"] else {}
        match = {}
        for p in profs:
            ev = m.evaluate(row, p, now)
            if ev:
                rs = sorted({r for c in ev["cands"] if c[0] == ev["level"] for r in c[2]})
                match[p["name"]] = {"level": ev["level"], "reasons": rs[:4]}
        pos = [{"unvan": p["unvan"], "types": p.get("kpss_score_types") or [], "min": p.get("min_score"),
                "il": ", ".join(f"{x['il']} ({x['kontenjan']})" for x in p["provinces"][:2])}
               for p in d.get("positions", [])[:4]]
        items.append({"id": row["guid"], "source": row["source"], "title": row["title"],
                      "institution": row["institution"], "type": row["ilan_type"],
                      "start": row["application_start"], "deadline": dl,
                      "url": row["url"] or m.DETAIL_URL.format(guid=row["guid"]),
                      "external": row["external_url"], "last_seen": row["last_seen"],
                      "refs": json.loads(row["source_refs"]) if row["source_refs"] else [], "positions": pos, "match": match})
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"generated": now.isoformat(timespec="seconds"), "profiles": [p["name"] for p in profs],
                   "ilanlar": items}, f, ensure_ascii=False)
    print(f"{len(items)} ilan -> {out}")
