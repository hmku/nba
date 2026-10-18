"""Train the value model on datagen output and write the artifact + model card.

  python -m fantasy.valuesearch.train --data train.jsonl [--holdout holdout.jsonl] --out data/models/value_search/value-model-0.1.0.npz

Rows = (league, team). Train/test split is by league (10% of training leagues held out, seed 0).
`--holdout`: independent leagues labelled with many more seasons (e.g. 2,000): the model is scored
against those near-exact simulator estimates, next to the Monte Carlo noise floor of the training
labels' own season count.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from fantasy.valuesearch import VALUE_SEARCH_VERSION
from fantasy.valuesearch.datagen import LABELS
from fantasy.valuesearch.features import FEATURES_VERSION, Featurizer
from fantasy.valuesearch.model import HGB_PARAMS, TARGETS, TreeEnsemble, ValueModel, fit_hgb

MODEL_VERSION = "value-model-0.1.0"


def load_leagues(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def featurize(feat: Featurizer, leagues: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """-> X (n*T, F), Y (n*T, 4), group (league seed), is_slot1 mask."""
    ctx = feat.ctx
    lg_rows = []
    for lg in leagues:
        ros = lg["rosters"]
        lg_rows.append([[ctx.pos[p] for p in ros[str(t)] if p in ctx.pos] for t in sorted(int(k) for k in ros)])
    X = np.concatenate([feat.transform(feat.pad(lg_rows[i : i + 256])) for i in range(0, len(lg_rows), 256)])
    T = X.shape[1]
    Y = np.array([lg["labels"] for lg in leagues], dtype=float)
    g = np.repeat([lg["seed"] for lg in leagues], T)
    slot1 = np.tile(np.arange(T) == 0, len(leagues))
    return X.reshape(-1, X.shape[2]), Y.reshape(-1, Y.shape[2]), g, slot1


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    r = y - p
    return {"rmse": float(np.sqrt((r**2).mean())), "mae": float(np.abs(r).mean()),
            "r2": float(1 - (r**2).sum() / ((y - y.mean()) ** 2).sum()), "corr": float(np.corrcoef(y, p)[0, 1]),
            "bias": float(r.mean()), "n": len(y)}


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
    ap.add_argument("--report", type=Path)
    a = ap.parse_args()
    t0 = time.time()
    s = load_settings()
    proj = load_projections()
    ctx = DraftContext(proj, s)
    feat = Featurizer(ctx, proj.df)
    leagues = load_leagues(a.data)
    X, Y, g, slot1 = featurize(feat, leagues)
    seeds = np.array(sorted({lg["seed"] for lg in leagues}))
    rng = np.random.default_rng(a.seed)
    test_seeds = set(rng.choice(seeds, size=max(1, len(seeds) // 10), replace=False).tolist())
    te = np.isin(g, list(test_seeds))
    report: dict = {"n_leagues": len(leagues), "n_rows": len(X), "n_test_leagues": len(test_seeds),
                    "regimes": {k: sum(1 for lg in leagues if lg["regime"] == k) for k in
                                sorted({lg["regime"] for lg in leagues})},
                    "seasons_per_league": sorted({lg["n_seasons"] for lg in leagues}), "split": {}, "holdout": {}}
    ens = {}
    for j, tgt in enumerate(LABELS):
        m = fit_hgb(X[~te], Y[~te, j], seed=a.seed)
        p = m.predict(X[te])
        report["split"][tgt] = {"all": metrics(Y[te, j], p), "slot1": metrics(Y[te & slot1, j], m.predict(X[te & slot1]))}
        ens[tgt] = m
    # refit on everything for the artifact
    final_sk = {tgt: fit_hgb(X, Y[:, j], seed=a.seed) for j, tgt in enumerate(LABELS)}
    final = {tgt: TreeEnsemble.from_sklearn(m) for tgt, m in final_sk.items()}
    vm = ValueModel(final, feat.names, {})
    if a.holdout:
        hl = load_leagues(a.holdout)
        Xh, Yh, _, s1h = featurize(feat, hl)
        ph = vm.predict(Xh)
        n_tr = report["seasons_per_league"][0]
        for j, tgt in enumerate(LABELS):
            y = Yh[:, j]
            d = {"all": metrics(y, ph[tgt]), "slot1": metrics(y[s1h], ph[tgt][s1h])}
            if tgt in ("p_playoffs", "p_title"):  # MC noise floor of an n_tr-season estimate of the same quantity
                d["mc_noise_rmse_at_train_seasons"] = float(np.sqrt((y * (1 - y)).mean() / n_tr))
            report["holdout"][tgt] = d
        report["holdout_n_leagues"] = len(hl)
        report["holdout_seasons"] = sorted({lg["n_seasons"] for lg in hl})
    Xc = X[:512]  # numpy inference must match sklearn
    sk_check = {tgt: float(np.abs(final_sk[tgt].predict(Xc) - final[tgt].predict(Xc)).max()) for tgt in LABELS}
    import sklearn

    card = {
        "model_version": MODEL_VERSION, "search_version": VALUE_SEARCH_VERSION, "features_version": FEATURES_VERSION,
        "created_at": datetime.now(UTC).isoformat(), "git_sha": git_sha(), "targets": TARGETS,
        "feature_names": feat.names, "learner": "sklearn HistGradientBoostingRegressor (squared error), numpy export",
        "sklearn_version": sklearn.__version__,
        "hyperparams": HGB_PARAMS,
        "train_seed": a.seed, "data": {"path": str(a.data.name), "league_seeds": [int(seeds.min()), int(seeds.max())],
                                       **{k: report[k] for k in ("n_leagues", "n_rows", "regimes", "seasons_per_league")}},
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
                      "check": sk_check}, indent=1))


if __name__ == "__main__":
    main()
