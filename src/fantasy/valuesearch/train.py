"""Train the value model on datagen output and write the artifact + model card.

  python -m fantasy.valuesearch.train --data train.jsonl [--holdout holdout.jsonl] --out data/models/value_search/value-model-0.1.0.npz
  python -m fantasy.valuesearch.train --data train.jsonl --holdout holdout.jsonl --variant fe --lam 1 \
      --version value-model-0.2.0 --out data/models/value_search/value-model-0.2.0.npz

Rows = (league, team). Train/test split is by league (10% of training leagues held out, seed 0); for paired
data (datagen --gen v2) by *room*, so the K completions of a room stay on one side.

Variants (value-model-0.2.0, docs/research/value_search.md "Value model 0.2.0"):
  plain  one HGB per target on the level labels (as 0.1.0).
  fel    as fe, but the correction is linear: a within-estimator (fixed-effects) ridge regression of the
         demeaned residual on the demeaned features, stored as a linear term next to the trees.
  fe     plain + a within-room correction (room fixed effects, one backfitting step): for the paired rows, h is
         an HGB fitted to the within-(room, team) demeaned residual of the plain model's cross-fitted (out-of-fold,
         by room) predictions, so it learns only what separates completions of the same room; the artifact is
         f + lam * h merged into one tree ensemble per target (FE_TARGETS only), so inference is unchanged.
`--holdout`: independent leagues labelled with many more seasons (e.g. 2,000): the model is scored
against those near-exact simulator estimates, next to the Monte Carlo noise floor of the training
labels' own season count.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from fantasy.valuesearch import VALUE_SEARCH_VERSION
from fantasy.valuesearch.datagen import LABELS
from fantasy.valuesearch.features import FEATURES_VERSION, Featurizer
from fantasy.valuesearch.model import HGB_PARAMS, TARGETS, TreeEnsemble, ValueModel, fit_hgb

MODEL_VERSION = "value-model-0.2.0"
FE_TARGETS = ("p_playoffs", "p_title")


def load_leagues(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def featurize(feat: Featurizer, leagues: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """-> X (n*T, F), Y (n*T, 4), group (room for paired data, else league seed), is_slot1 mask."""
    ctx = feat.ctx
    lg_rows = []
    for lg in leagues:
        ros = lg["rosters"]
        lg_rows.append([[ctx.pos[p] for p in ros[str(t)] if p in ctx.pos] for t in sorted(int(k) for k in ros)])
    X = np.concatenate([feat.transform(feat.pad(lg_rows[i : i + 256])) for i in range(0, len(lg_rows), 256)])
    T = X.shape[1]
    Y = np.array([lg["labels"] for lg in leagues], dtype=float)
    g = np.repeat([lg.get("room", lg["seed"]) for lg in leagues], T)
    slot1 = np.tile(np.arange(T) == 0, len(leagues))
    return X.reshape(-1, X.shape[2]), Y.reshape(-1, Y.shape[2]), g, slot1


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    r = y - p
    return {"rmse": float(np.sqrt((r**2).mean())), "mae": float(np.abs(r).mean()),
            "r2": float(1 - (r**2).sum() / ((y - y.mean()) ** 2).sum()), "corr": float(np.corrcoef(y, p)[0, 1]),
            "bias": float(r.mean()), "n": len(y)}


def within_room(pred: np.ndarray, y: np.ndarray, g: np.ndarray, team: np.ndarray, cand: np.ndarray,
                mask: np.ndarray | None = None) -> dict:
    """Within-(room, team) differences candidate k - candidate 0 (k >= 1): corr(predicted, labelled), sign
    agreement over pairs whose labels differ, n pairs. `mask` restricts rows (e.g. slot 1 = my team)."""
    m = np.ones(len(y), dtype=bool) if mask is None else mask
    base = {(gg, tt): i for i, (gg, tt, cc) in enumerate(zip(g, team, cand, strict=True)) if m[i] and cc == 0}
    dp, dy = [], []
    for i in np.flatnonzero(m & (cand > 0)):
        j = base.get((g[i], team[i]))
        if j is not None:
            dp.append(pred[i] - pred[j])
            dy.append(y[i] - y[j])
    dp_a, dy_a = np.array(dp), np.array(dy)
    nz = np.abs(dy_a) > 1e-12
    return {"corr": float(np.corrcoef(dp_a, dy_a)[0, 1]) if len(dp_a) > 2 else float("nan"),
            "sign_agree": float((np.sign(dp_a[nz]) == np.sign(dy_a[nz])).mean()) if nz.any() else float("nan"),
            "n": len(dp_a), "n_nonzero": int(nz.sum())}


def fit_variant(X: np.ndarray, Y: np.ndarray, g: np.ndarray, team: np.ndarray, paired: np.ndarray,
                variant: str = "plain", lam: float = 1.0, seed: int = 0, folds: int = 4,
                hgb_kw: dict | None = None, weight: np.ndarray | None = None,
                fe_mask: np.ndarray | None = None, ridge: float = 1e-3,
                sk_out: dict | None = None) -> dict[str, TreeEnsemble]:
    """Fit one ensemble per target (see module doc for the variants). `weight`: sample weights of the level
    fits; `fe_mask`: rows the within-room correction is fitted on (default all paired rows); `sk_out`: if given,
    receives the fitted sklearn level estimators per target (for the numpy-vs-sklearn parity check)."""
    kw = hgb_kw or {}
    level = {tgt: fit_hgb(X, Y[:, j], seed=seed, sample_weight=weight, **kw) for j, tgt in enumerate(LABELS)}
    if sk_out is not None:
        sk_out.update(level)
    out = {tgt: TreeEnsemble.from_sklearn(est) for tgt, est in level.items()}
    if variant == "plain":
        return out
    if variant not in ("fe", "fel"):
        raise ValueError(variant)
    rooms = np.unique(g[paired])
    fold_of = dict(zip(rooms, np.random.default_rng([seed, 5]).permutation(len(rooms)) % folds, strict=True))
    fold = np.array([fold_of.get(x, -1) for x in g])
    key = g.astype(np.int64) * 100 + team
    for j, tgt in enumerate(LABELS):
        if tgt not in FE_TARGETS:
            continue
        oof = np.zeros(len(Y))
        for f in range(folds):
            te = fold == f
            w = None if weight is None else weight[~te]
            oof[te] = fit_hgb(X[~te], Y[~te, j], seed=seed, sample_weight=w, **kw).predict(X[te])
        r = Y[:, j] - oof
        idx = np.flatnonzero(paired & (fe_mask if fe_mask is not None else True))
        _, inv = np.unique(key[idx], return_inverse=True)
        cnt = np.bincount(inv)
        rd = r[idx] - (np.bincount(inv, weights=r[idx]) / cnt)[inv]
        if variant == "fe":
            h = TreeEnsemble.from_sklearn(fit_hgb(X[idx], rd, seed=seed, **kw))
            out[tgt] = TreeEnsemble.combine(out[tgt], h, lam)
            continue
        # fel: within (fixed-effects) ridge regression of the demeaned residual on demeaned features
        Xi = np.nan_to_num(X[idx])
        Xd = Xi - (np.stack([np.bincount(inv, weights=Xi[:, c]) for c in range(Xi.shape[1])], axis=1) / cnt[:, None])[inv]
        sd = Xd.std(axis=0)
        sd[sd < 1e-9] = np.inf
        Z = Xd / sd
        w = np.linalg.solve(Z.T @ Z + ridge * len(Z) * np.eye(Z.shape[1]), Z.T @ rd) / sd
        w = lam * w
        e = out[tgt]
        e.lin_w = w
        e.baseline -= float(Xi.mean(axis=0) @ w)  # no level shift on average over the paired rows
    return out


def main() -> None:
    from fantasy.draft.optimizer import DraftContext
    from fantasy.league.settings import load_settings
    from fantasy.model.lines import load_projections
    from fantasy.model.valuation import git_sha

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--holdout", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--variant", choices=["plain", "fe", "fel"], default="plain")
    ap.add_argument("--ridge", type=float, default=1e-3)
    ap.add_argument("--fe-rows", choices=["all", "slot1"], default="all")
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--version", default=MODEL_VERSION)
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    t0 = time.time()
    s = load_settings()
    proj = load_projections()
    ctx = DraftContext(proj, s)
    feat = Featurizer(ctx, proj.df)
    leagues = load_leagues(a.data)
    X, Y, g, slot1 = featurize(feat, leagues)
    T = s.num_teams
    team = np.tile(np.arange(T), len(leagues))
    cand = np.repeat([lg.get("cand", 0) for lg in leagues], T)
    paired = np.repeat(["cand" in lg for lg in leagues], T)
    seeds = np.array(sorted(set(g.tolist())))
    rng = np.random.default_rng(a.seed)
    test_seeds = set(rng.choice(seeds, size=max(1, len(seeds) // 10), replace=False).tolist())
    te = np.isin(g, list(test_seeds))
    report: dict = {"n_leagues": len(leagues), "n_rooms": len(seeds), "n_rows": len(X),
                    "n_test_groups": len(test_seeds), "variant": a.variant, "lam": a.lam,
                    "regimes": {k: sum(1 for lg in leagues if lg["regime"] == k) for k in
                                sorted({lg["regime"] for lg in leagues})},
                    "seasons_per_league": sorted({lg["n_seasons"] for lg in leagues}), "split": {}, "holdout": {}}
    fm = slot1 if a.fe_rows == "slot1" else None
    ens = fit_variant(X[~te], Y[~te], g[~te], team[~te], paired[~te], a.variant, a.lam, a.seed,
                      fe_mask=None if fm is None else fm[~te], ridge=a.ridge)
    for j, tgt in enumerate(LABELS):
        p = ens[tgt].predict(X[te])
        report["split"][tgt] = {"all": metrics(Y[te, j], p), "slot1": metrics(Y[te & slot1, j], p[slot1[te]])}
        if paired[te].any():
            report["split"][tgt]["within_room_slot1"] = within_room(p, Y[te, j], g[te], team[te], cand[te], slot1[te])
    final_sk: dict = {}
    final = fit_variant(X, Y, g, team, paired, a.variant, a.lam, a.seed, fe_mask=fm, ridge=a.ridge,
                        sk_out=final_sk)  # refit on everything for the artifact
    # numpy inference must match sklearn; only the plain variant is a pure export of the sklearn level model
    # (fe/fel add a correction sklearn does not compute)
    Xc = X[:512]
    sk_check = ({tgt: float(np.abs(final_sk[tgt].predict(Xc) - final[tgt].predict(Xc)).max()) for tgt in LABELS}
                if a.variant == "plain" else None)
    if sk_check is not None and max(sk_check.values()) > 1e-6:
        print(f"WARNING: numpy export disagrees with sklearn (max abs diff {sk_check}); do not ship this artifact",
              file=sys.stderr)
    vm = ValueModel(final, feat.names, {})
    if a.holdout:
        hl = load_leagues(a.holdout)
        Xh, Yh, gh, s1h = featurize(feat, hl)
        ph = vm.predict(Xh)
        th = np.tile(np.arange(T), len(hl))
        ch = np.repeat([lg.get("cand", 0) for lg in hl], T)
        n_tr = report["seasons_per_league"][0]
        for j, tgt in enumerate(LABELS):
            y = Yh[:, j]
            d = {"all": metrics(y, ph[tgt]), "slot1": metrics(y[s1h], ph[tgt][s1h])}
            if tgt in ("p_playoffs", "p_title"):  # MC noise floor of an n_tr-season estimate of the same quantity
                d["mc_noise_rmse_at_train_seasons"] = float(np.sqrt((y * (1 - y)).mean() / n_tr))
            if (ch > 0).any():
                d["within_room_slot1"] = within_room(ph[tgt], y, gh, th, ch, s1h)
            report["holdout"][tgt] = d
        report["holdout_n_leagues"] = len(hl)
        report["holdout_seasons"] = sorted({lg["n_seasons"] for lg in hl})
    import sklearn

    from fantasy.draft.optimizer import OPTIMIZER_VERSION
    from fantasy.model.blend import PROJECTIONS_VERSION
    from fantasy.model.noise import NOISE_MODEL_VERSION
    from fantasy.model.valuation import MODEL_VERSION as VALUATION_VERSION
    from fantasy.sim import SIM_VERSION

    gens = sorted({lg.get("gen", "v1") for lg in leagues})
    card = {
        "model_version": a.version, "search_version": VALUE_SEARCH_VERSION, "features_version": FEATURES_VERSION,
        "created_at": datetime.now(UTC).isoformat(), "git_sha": git_sha(), "targets": TARGETS,
        "feature_names": feat.names, "learner": "sklearn HistGradientBoostingRegressor (squared error), numpy export",
        "variant": a.variant, "lam": a.lam if a.variant != "plain" else None,
        "fe_targets": list(FE_TARGETS) if a.variant != "plain" else [],
        "fe_rows": a.fe_rows if a.variant != "plain" else None, "ridge": a.ridge if a.variant == "fel" else None,
        "sklearn_version": sklearn.__version__,
        "hyperparams": HGB_PARAMS,
        "versions": {"valuation": VALUATION_VERSION, "season_sim": SIM_VERSION, "noise": NOISE_MODEL_VERSION,
                     "optimizer": OPTIMIZER_VERSION, "projections": PROJECTIONS_VERSION},
        "train_seed": a.seed, "data": {"path": str(a.data.name), "generator": gens,
                                       "league_seeds": [int(seeds.min()), int(seeds.max())],
                                       **{k: report[k] for k in ("n_leagues", "n_rooms", "n_rows", "regimes",
                                                                 "seasons_per_league")}},
        "projections_sha256": proj.sha256, "metrics": {"league_split_test": report["split"], "holdout": report["holdout"],
                                                       "holdout_n_leagues": report.get("holdout_n_leagues"),
                                                       "holdout_seasons": report.get("holdout_seasons")},
        "numpy_vs_sklearn_max_abs_diff": sk_check, "train_seconds": round(time.time() - t0, 1),
    }
    vm.card = card
    vm.save(a.out)
    if a.report:
        a.report.write_text(json.dumps(card, indent=2))
    print(json.dumps({"split": report["split"], "holdout": report["holdout"], "size_bytes": a.out.stat().st_size,
                      "check": sk_check},
                     indent=1))


if __name__ == "__main__":
    main()
