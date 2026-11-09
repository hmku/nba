"""League summary + simulator calibration for weeks 1-3. Inputs: wr/w{W}_t{yahoo}.json (fantasy review week --team),
preds*.json (sim_weekstart.py), season_sims.json (prod model_runs). Writes analysis.json and prints tables."""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import chi2

SP = Path(__file__).parent
PRED = sys.argv[1] if len(sys.argv) > 1 else "preds.json"
CATS = ["fg_pct", "ft_pct", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
RAW = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
SLOT2Y = {1: 11, 2: 9, 3: 1, 4: 7, 5: 2, 6: 10, 7: 6, 8: 12, 9: 3, 10: 4, 11: 8, 12: 5}
Y2SLOT = {v: k for k, v in SLOT2Y.items()}
sched = json.load(open("/Users/harrisonku/dev/nba/data/manual/league_schedule.json"))
NAMES = json.load(open("/Users/harrisonku/dev/nba/data/manual/league_rosters_2026-27.json"))
NAMES = {int(k): v["name"] for k, v in NAMES["teams"].items()}
preds = json.load(open(SP / PRED))
rng = np.random.default_rng(0)


def catval(t, c):
    if c == "fg_pct":
        return t["fgm"] / t["fga"] if t["fga"] else 0.0
    if c == "ft_pct":
        return t["ftm"] / t["fta"] if t["fta"] else 0.0
    return t[c]


def winner(a, b, c):  # +1 a wins, -1 b wins, 0 tie (TOV lower wins)
    x, y = catval(a, c), catval(b, c)
    if c in ("fg_pct", "ft_pct"):
        x, y = round(x, 4), round(y, 4)
    s = np.sign(x - y)
    return -s if c == "tov" else s


rev = {}
for w in (1, 2, 3):
    for y in range(1, 13):
        rev[(w, y)] = json.load(open(SP / f"wr/w{w}_t{y}.json"))["review"]

# ---------- reconstruction validation on my matchups (Yahoo final available for both sides)
valid = []
for w in (1, 2, 3):
    r = rev[(w, 11)]
    for side in ("me", "opp"):
        yah = r["final"]["totals"][side]
        rec = r["teams"][side]["totals"]
        valid.append({"week": w, "team": r[side if side == "me" else "opponent"]["slot"],
                      "games_rec": r["teams"][side]["games_counted"],
                      "games_yahoo": (r["reconciliation"] or {}).get(side, {}).get("yahoo", {}).get("games"),
                      "err": {k: rec[k] - yah[k] for k in RAW},
                      "rel_err": {k: (rec[k] - yah[k]) / yah[k] for k in RAW if yah[k]},
                      "pct_err": {"fg_pct": catval(rec, "fg_pct") - catval(yah, "fg_pct"),
                                  "ft_pct": catval(rec, "ft_pct") - catval(yah, "ft_pct")}})
    # does reconstruction flip any category of my matchup?
    a, b = r["teams"]["me"]["totals"], r["teams"]["opp"]["totals"]
    ya, yb = r["final"]["totals"]["me"], r["final"]["totals"]["opp"]
    flips = [c for c in CATS if winner(a, b, c) != winner(ya, yb, c)]
    valid.append({"week": w, "flips": flips})

# ---------- outcomes per matchup
matchups = []
for w in (1, 2, 3):
    for a, b in next(x["matchups"] for x in sched["weeks"] if x["week"] == w):
        ya, yb = SLOT2Y[a], SLOT2Y[b]
        ra = rev[(w, ya)]
        yahoo = 11 in (ya, yb)
        if yahoo:
            r = rev[(w, 11)]
            me_is_a = (ya == 11)
            ta = r["final"]["totals"]["me" if me_is_a else "opp"]
            tb = r["final"]["totals"]["opp" if me_is_a else "me"]
        else:
            ta, tb = ra["teams"]["me"]["totals"], rev[(w, yb)]["teams"]["me"]["totals"]
        res = {c: int(winner(ta, tb, c)) for c in CATS}
        wa, wb = sum(v > 0 for v in res.values()), sum(v < 0 for v in res.values())
        pm = next(m for m in preds[str(w)]["matchups"] if {m["a"], m["b"]} == {a, b})
        flip = pm["a"] != a
        cw = {c: (1 - pm["cat_win"][c] - pm["cat_tie"][c]) if flip else pm["cat_win"][c] for c in CATS}
        ct = pm["cat_tie"]
        pw = pm["p_loss"] if flip else pm["p_win"]
        matchups.append({"week": w, "a": a, "b": b, "basis": "yahoo_final" if yahoo else "reconstructed",
                         "tot": {a: ta, b: tb}, "games": {a: (rev[(w, ya)]["teams"]["me"]["games_counted"]),
                                                          b: (rev[(w, yb)]["teams"]["me"]["games_counted"])},
                         "cat_res": res, "score": [wa, wb], "p_win_a": pw, "p_tie": pm["p_tie"],
                         "p_cat_a": cw, "p_cat_tie": ct,
                         "exp_cats_a": (9 - pm["exp_cats"]) if flip else pm["exp_cats"]})

# ---------- standings
stand = {t: {"W": 0, "L": 0, "T": 0, "cw": 0, "cl": 0, "ct": 0, "cat": {c: [0, 0, 0] for c in CATS}, "weeks": []}
         for t in range(1, 13)}
for m in matchups:
    for t, o, sgn in ((m["a"], m["b"], 1), (m["b"], m["a"], -1)):
        s = stand[t]
        wins = m["score"][0] if sgn == 1 else m["score"][1]
        losses = m["score"][1] if sgn == 1 else m["score"][0]
        s["cw"] += wins; s["cl"] += losses; s["ct"] += 9 - wins - losses
        s["W" if wins > losses else ("L" if wins < losses else "T")] += 1
        s["weeks"].append(f"W{m['week']} {'W' if wins > losses else ('L' if wins < losses else 'T')} {wins}-{losses}"
                          f"{'-' + str(9 - wins - losses) if 9 - wins - losses else ''} v{o}"
                          f"{'' if m['basis'] == 'yahoo_final' else '*'}")
        for c in CATS:
            v = m["cat_res"][c] * sgn
            s["cat"][c][0 if v > 0 else (1 if v < 0 else 2)] += 1

# ---------- matchup-level calibration
def brier(p, y):
    p, y = np.asarray(p), np.asarray(y)
    return float(np.mean((p - y) ** 2))


pm_ = np.array([m["p_win_a"] + 0.5 * m["p_tie"] for m in matchups])
ym_ = np.array([1.0 if m["score"][0] > m["score"][1] else (0.0 if m["score"][0] < m["score"][1] else 0.5)
                for m in matchups])
# category level
P, Y, G, CC = [], [], [], []
for gi, m in enumerate(matchups):
    for c in CATS:
        P.append(m["p_cat_a"][c] + 0.5 * m["p_cat_tie"][c])
        Y.append({1: 1.0, -1: 0.0, 0: 0.5}[m["cat_res"][c]])
        G.append(gi); CC.append(c)
P, Y, G, CC = map(np.array, (P, Y, G, CC))


def fit_slope(p, y):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    L = np.log(p / (1 - p))
    def nll(b):
        q = np.clip(1 / (1 + np.exp(-b * L)), 1e-9, 1 - 1e-9)
        return -np.sum(y * np.log(q) + (1 - y) * np.log(1 - q))
    return float(minimize_scalar(nll, bounds=(0.01, 5), method="bounded").x)


def cluster_boot(fn, groups, B=4000):
    ug = np.unique(groups)
    vals = []
    for _ in range(B):
        pick = rng.choice(ug, len(ug), replace=True)
        idx = np.concatenate([np.where(groups == g)[0] for g in pick])
        vals.append(fn(idx))
    return float(np.std(vals, ddof=1)), np.percentile(vals, [5, 95]).tolist()


def calib(p, y, groups):
    fav = np.where(p >= 0.5, p, 1 - p); yf = np.where(p >= 0.5, y, 1 - y)
    out = {"n": len(p), "brier": brier(p, y), "brier_climo": brier(np.full(len(p), 0.5), y),
           "logloss": float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1)))),
           "fav_pred": float(fav.mean()), "fav_actual": float(yf.mean()),
           "slope": fit_slope(p, y),
           # expected Brier if the model were calibrated (sum p(1-p)/n) - compare to observed
           "brier_if_calibrated": float(np.mean(p * (1 - p)))}
    out["fav_gap_se"], _ = cluster_boot(lambda i: float((yf[i] - fav[i]).mean()), groups)
    out["slope_se"], out["slope_90ci"] = cluster_boot(lambda i: fit_slope(p[i], y[i]), groups, B=1000)
    out["brier_gap_se"], _ = cluster_boot(lambda i: float(np.mean((p[i] - y[i]) ** 2 - p[i] * (1 - p[i]))), groups)
    # reliability bins on favourite-oriented probabilities
    bins = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
    out["bins"] = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        k = (fav >= lo) & (fav < hi)
        if k.sum():
            out["bins"].append({"bin": f"{lo:.1f}-{min(hi,1):.1f}", "n": int(k.sum()), "pred": round(float(fav[k].mean()), 3),
                                "actual": round(float(yf[k].mean()), 3),
                                "binom_se": round(float(np.sqrt(fav[k].mean() * (1 - fav[k].mean()) / k.sum())), 3)})
    return out


cal_match = calib(pm_, ym_, np.arange(len(pm_)))
cal_cat = calib(P, Y, G)
cal_cat_by = {}
for c in CATS:
    k = CC == c
    pc, yc = P[k], Y[k]
    fav = np.where(pc >= 0.5, pc, 1 - pc); yf = np.where(pc >= 0.5, yc, 1 - yc)
    cal_cat_by[c] = {"brier": round(brier(pc, yc), 3), "brier_if_cal": round(float(np.mean(pc * (1 - pc))), 3),
                     "fav_pred": round(float(fav.mean()), 3), "fav_actual": round(float(yf.mean()), 3)}
# expected cat wins per matchup side vs actual
exp_cats_err = [(m["score"][0] + 0.5 * (9 - sum(m["score"]))) - m["exp_cats_a"] for m in matchups]

# ---------- per-category total bias with the sim's weekly SD (team-week level, 36)
zrows = []
for m in matchups:
    w = m["week"]
    for t in (m["a"], m["b"]):
        pr = preds[str(w)]["teams"][str(t)]
        tot = m["tot"][t]
        row = {"week": w, "team": t, "basis": m["basis"], "games": m["games"][t], "games_pred": pr["games"],
               "games_sd": pr["games_sd"]}
        for c in CATS:
            a = catval(tot, c)
            row[c] = {"actual": a, "mean": pr[c]["mean"], "sd": pr[c]["sd"], "z": (a - pr[c]["mean"]) / pr[c]["sd"]}
        zrows.append(row)
zsum = {}
for c in CATS + ["games"]:
    if c == "games":
        z = np.array([(r["games"] - r["games_pred"]) / r["games_sd"] for r in zrows])
        d = np.array([r["games"] - r["games_pred"] for r in zrows])
    else:
        z = np.array([r[c]["z"] for r in zrows]); d = np.array([r[c]["actual"] - r[c]["mean"] for r in zrows])
    n = len(z)
    # var(z) 90% CI under normal iid
    v = float(np.var(z, ddof=1))
    zsum[c] = {"mean_diff": float(d.mean()), "mean_z": float(z.mean()), "mean_z_se": float(z.std(ddof=1) / np.sqrt(n)),
               "var_z": v, "var_z_90ci": [v * (n - 1) / chi2.ppf(0.95, n - 1), v * (n - 1) / chi2.ppf(0.05, n - 1)],
               "mean_z_yahoo_only": float(np.mean([((r["games"] - r["games_pred"]) / r["games_sd"]) if c == "games"
                                                   else r[c]["z"] for r in zrows if r["basis"] == "yahoo_final"]))}
# per-week mean z (league environment)
zweek = {w: {c: round(float(np.mean([r[c]["z"] for r in zrows if r["week"] == w])), 2) for c in CATS} for w in (1, 2, 3)}

# ---------- weekreview rate z (actual vs projection GIVEN the games played; per-game noise model)
rz = {c: [] for c in CATS}
vol = []
for (w, y), r in rev.items():
    t = r["teams"]["me"]
    for c in CATS:
        if t["categories"][c]["z"] is not None:
            rz[c].append(t["categories"][c]["z"])
    vol.append((w, Y2SLOT[y], t["games_counted"], t["games_expected_week_start"]))
rate = {c: {"mean_z": float(np.mean(v)), "se": float(np.std(v, ddof=1) / np.sqrt(len(v))), "var_z": float(np.var(v, ddof=1))}
        for c, v in rz.items()}

out = {"validation": valid, "matchups": matchups, "standings": stand, "cal_match": cal_match, "cal_cat": cal_cat,
       "cal_cat_by": cal_cat_by, "exp_cats_err_mean": float(np.mean(exp_cats_err)),
       "exp_cats_err_se": float(np.std(exp_cats_err, ddof=1) / np.sqrt(len(exp_cats_err))),
       "exp_cats_err_sd": float(np.std(exp_cats_err, ddof=1)),
       "zsum": zsum, "zweek": zweek, "zrows": zrows, "rate_z": rate, "volume": vol}
(SP / ("analysis.json" if PRED == "preds.json" else "analysis_" + PRED)).write_text(json.dumps(out, indent=1, default=float))

# ---------- print
print("VALIDATION (reconstructed - Yahoo final)")
for v in valid:
    if "err" in v:
        print(v["week"], v["team"], "games", v["games_rec"], v["games_yahoo"], {k: round(x, 1) for k, x in v["err"].items()},
              {k: round(x, 4) for k, x in v["pct_err"].items()})
    else:
        print("  week", v["week"], "category flips:", v["flips"])
print("\nSTANDINGS")
order = sorted(stand, key=lambda t: (-(stand[t]["W"] + 0.5 * stand[t]["T"]), -(stand[t]["cw"] + 0.5 * stand[t]["ct"])))
for t in order:
    s = stand[t]
    print(f"{t:>2} {NAMES[t][:24]:<24} {s['W']}-{s['L']}-{s['T']}  cats {s['cw']}-{s['cl']}-{s['ct']}  "
          + " ".join(f"{c}:{s['cat'][c][0]}-{s['cat'][c][1]}" for c in CATS) + "  | " + "; ".join(s["weeks"]))
print("\nMATCHUPS")
for m in matchups:
    print(m["week"], m["a"], m["b"], m["basis"][:5], m["score"], "pwin_a", round(m["p_win_a"], 3), "expcats_a",
          round(m["exp_cats_a"], 2), "games", m["games"])
for k, cal in (("MATCHUP", cal_match), ("CATEGORY", cal_cat)):
    print("\n" + k, {a: (round(b, 3) if isinstance(b, float) else b) for a, b in cal.items() if a != "bins"})
    for b in cal["bins"]:
        print("   ", b)
print("\nper-cat calibration", cal_cat_by)
print("exp cats won err (actual - pred) per side", round(out["exp_cats_err_mean"], 3), "+-", round(out["exp_cats_err_se"], 3),
      "sd", round(out["exp_cats_err_sd"], 3))
print("\nTOTALS z vs sim weekly distribution (36 team-weeks)")
for c, s in zsum.items():
    print(f"  {c:7} diff {s['mean_diff']:+8.4f}  mean z {s['mean_z']:+.2f} ± {s['mean_z_se']:.2f}  var z {s['var_z']:.2f} "
          f"[{s['var_z_90ci'][0]:.2f},{s['var_z_90ci'][1]:.2f}]  yahoo-only mean z {s['mean_z_yahoo_only']:+.2f}")
print("per-week mean z", zweek)
print("\nRATE z (weekreview: actual vs projection given games, per-game noise)")
for c, s in rate.items():
    print(f"  {c:7} mean z {s['mean_z']:+.2f} ± {s['se']:.2f}  var z {s['var_z']:.2f}")
