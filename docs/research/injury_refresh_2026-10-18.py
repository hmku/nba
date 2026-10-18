"""Record of the 2026-10-18 injury-override refresh (one-off; decisions in DECIDE). Run from repo root."""
import csv, io, json, sys
from datetime import date
sys.path.insert(0, "src")
from fantasy.data.ids import canonical_id
from fantasy.data.injuries import parse_espn, parse_cbs, games_missed_from_return, model_status
from fantasy.data.provenance import RAW_DIR
import pandas as pd

AS_OF = date(2026, 10, 18)
# name -> (games_missed or None for ESPN-implied, rationale). Rule: explicit timeline in the report
# -> derived from it; generic "not ready for start" placeholder -> mean of ESPN-implied and prior estimate.
DECIDE = {
    "Jimmy Butler III": (49, "Jan-Feb return window -> midpoint ~Feb 1"),
    "Mark Williams": (None, "labrum surgery Sep 11; ESPN est. Feb 3"),
    "Donte DiVincenzo": (65, "ESPN est. Apr 2 is a placeholder; player expects return in-season -> keep prior 65"),
    "Shaedon Sharpe": (60, "torn meniscus, ~6 months from Aug 24 -> ~late Feb"),
    "Jordan Miller": (None, "rotator cuff, re-eval 6 months from Aug 26"),
    "Moses Moody": (34, "Dec-Jan return window -> midpoint ~Jan 1"),
    "Kristaps Porzingis": (17, "undisclosed illness, back at facility Oct 15; ESPN placeholder 9 vs prior 25 -> mean"),
    "Brandon Ingram": (17, "partial Achilles tear, not ready for start; ESPN placeholder 9 vs prior 25 -> mean"),
    "Bradley Beal": (12, "knee, not ready for start; ESPN placeholder 9 vs prior 15 -> mean"),
    "Max Strus": (12, "plantar fascia, re-eval 4 weeks from Oct 5 (re-eval, not return) -> keep prior 12"),
    "Isaiah Stewart": (2, "ankle, at least two weeks from Oct 9"),
    "Jusuf Nurkic": (8, "toe, at least 4 weeks from Oct 7 -> keep prior 8"),
    "Maxime Raynaud": (7, "knee, at least first 5-6 games, potentially longer"),
    "Julian Strawther": (22, "knee sprain, re-eval in 6 weeks from Oct 17"),
    "Coby White": (3, "calf strain, no preseason; ESPN est. Oct 21 vs prior 6 -> mean"),
    "Kon Knueppel": (1, "hamstring, opener in doubt"),
    "Nic Claxton": (1, "hamstring, re-eval before opener"),
}

e = parse_espn(RAW_DIR / "espn" / "injuries.json")
cbs = set(parse_cbs(RAW_DIR / "cbs" / "injuries.html")["player_id"])
proj = set(pd.read_csv("data/processed/projections_2026_27.csv")["player_id"])
path = "data/manual/player_overrides_2026_27.csv"
lines = open(path).read().splitlines()
comments = [l for l in lines if l.startswith("#")]
rows = list(csv.DictReader([l for l in lines if not l.startswith("#")]))
fields = list(rows[0].keys())
by_id = {}
for r in rows:  # injury-bearing row wins when a player has several rows
    pid = canonical_id(r["name"])
    if pid not in by_id or r["status"] or r["games_missed"]:
        by_id[pid] = r

changes = []
for _, x in e.iterrows():
    pid = x["player_id"]
    if pid not in proj:
        continue
    implied = games_missed_from_return(x["return_date"], AS_OF)
    gm, why = DECIDE.get(x["name"], (None, ""))
    if gm is None:
        gm = implied if x["status"] == "Out" else None
        why = why or ("ESPN est. return " + x["return_date"][:10] if gm else "day-to-day for opener")
    status = model_status(x["status"], x["fantasy_status"])
    note = f"{x['injury'] or 'injury'}: {why}"
    src = (f"ESPN injuries API {AS_OF} (upd {x['updated']}, est return {x['return_date'][:10]})"
           + ("; CBS lists" if pid in cbs else "; not on CBS"))
    r = by_id.get(pid)
    if r is None:
        r = {f: "" for f in fields}
        r["name"] = x["name"]
        rows.append(r)
        by_id[pid] = r
        old = ("", "")
    else:
        old = (r["status"], r["games_missed"])
    new = (status, "" if gm in (None, 0) else str(gm))
    if r["source"] and not r["status"] and not r["games_missed"]:
        src = r["source"] + " | injury: " + src  # keep the row's non-injury provenance
    elif r["source"]:
        src = src + " (prior: " + r["source"] + ")"
    r["status"], r["games_missed"], r["injury_note"], r["source"] = new[0], new[1], note, src
    if old != new:
        changes.append({"name": x["name"], "old_status": old[0], "old_gm": old[1],
                        "new_status": new[0], "new_gm": new[1], "espn_implied": implied})

listed = set(e["player_id"])
stale = [r["name"] for pid, r in by_id.items() if r["status"] in ("out", "gtd") and pid not in listed]

buf = io.StringIO()
w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
w.writeheader(); w.writerows(rows)
comments.append("# Injury rows refreshed 2026-10-18 from the ESPN injuries API (CBS cross-check); see source column.")
open(path, "w").write("\n".join(comments) + "\n" + buf.getvalue())
print(json.dumps({"changes": changes, "out_or_gtd_not_listed_by_espn": stale}, indent=1))
