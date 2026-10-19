"""Value model: league features (fantasy.valuesearch.features) -> a team's season outcomes.

Training uses scikit-learn's HistGradientBoostingRegressor (optional extra `valuesearch`), one model
per target (P(playoffs), P(title), expected rank, expected regular-season category wins), squared
error on the simulator's per-league Monte Carlo estimates (unbiased, noisy labels).

The artifact is a plain `.npz` (tree node arrays + baseline per target) and a JSON model card, so
draft-time inference is pure numpy (no sklearn, no pickle): `ValueModel.load(path).predict(X)`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from fantasy.league.settings import REPO_ROOT

MODEL_DIR = REPO_ROOT / "data" / "models" / "value_search"
DEFAULT_MODEL = MODEL_DIR / "value-model-0.2.0.npz"  # 0.1.0 kept for rollback (MODEL_ENV)
# rollback / override without a code change: FANTASY_VALUE_MODEL=value-model-0.1.0 (a version in MODEL_DIR) or a path
MODEL_ENV = "FANTASY_VALUE_MODEL"


def default_model_path() -> Path:
    v = os.environ.get(MODEL_ENV, "").strip()
    if not v:
        return DEFAULT_MODEL
    p = Path(v)
    return p if (p.suffix == ".npz" or p.exists()) else MODEL_DIR / f"{v}.npz"
TARGETS = ["p_playoffs", "p_title", "e_rank", "e_cat_wins"]
OBJECTIVES = ("blend", "title", "playoffs", "rank")
# blend = W_TITLE * P(title) + W_PLAYOFFS * P(playoffs): a prior, see docs/research/value_search.md
W_TITLE, W_PLAYOFFS = 0.7, 0.3


def objective_value(pred: dict[str, np.ndarray], objective: str = "blend") -> np.ndarray:
    if objective == "blend":
        return W_TITLE * pred["p_title"] + W_PLAYOFFS * pred["p_playoffs"]
    if objective == "title":
        return pred["p_title"]
    if objective == "playoffs":
        return pred["p_playoffs"]
    if objective == "rank":
        return -pred["e_rank"]
    raise ValueError(f"unknown objective {objective!r}; valid: {', '.join(OBJECTIVES)}")


@dataclass
class TreeEnsemble:
    """Numpy evaluation of a fitted sklearn HistGradientBoostingRegressor (numeric features only)."""

    baseline: float
    feature: np.ndarray  # (n_trees, n_nodes) int
    threshold: np.ndarray  # (n_trees, n_nodes) float
    left: np.ndarray
    right: np.ndarray
    missing_left: np.ndarray  # bool
    is_leaf: np.ndarray  # bool
    value: np.ndarray  # float
    depth: int
    # optional linear term lin_w . X (value-model-0.2.0 "fel" variant: within-room fixed-effects correction)
    lin_w: np.ndarray | None = None

    @classmethod
    def from_sklearn(cls, est) -> TreeEnsemble:  # noqa: ANN001
        preds = [p[0] for p in est._predictors]
        n = max(len(p.nodes) for p in preds)

        def stack(field: str, fill: float, dtype: type) -> np.ndarray:
            out = np.full((len(preds), n), fill, dtype=dtype)
            for i, p in enumerate(preds):
                out[i, : len(p.nodes)] = p.nodes[field]
            return out

        if any(p.nodes["is_categorical"].any() for p in preds):
            raise ValueError("categorical splits not supported")
        return cls(
            baseline=float(np.ravel(est._baseline_prediction)[0]),
            feature=stack("feature_idx", 0, np.int32), threshold=stack("num_threshold", 0.0, np.float64),
            left=stack("left", 0, np.int32), right=stack("right", 0, np.int32),
            missing_left=stack("missing_go_to_left", 0, np.bool_), is_leaf=stack("is_leaf", 1, np.bool_),
            value=stack("value", 0.0, np.float64), depth=int(max(p.nodes["depth"].max() for p in preds)),
        )

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        n_trees = self.feature.shape[0]
        t = np.arange(n_trees)[:, None]
        node = np.zeros((n_trees, X.shape[0]), dtype=np.int32)
        rows = np.arange(X.shape[0])[None, :]
        for _ in range(self.depth + 1):
            leaf = self.is_leaf[t, node]
            if leaf.all():
                break
            f = self.feature[t, node]
            x = X[rows, f]
            go_left = np.where(np.isnan(x), self.missing_left[t, node], x <= self.threshold[t, node])
            nxt = np.where(go_left, self.left[t, node], self.right[t, node])
            node = np.where(leaf, node, nxt)
        out = self.baseline + self.value[t, node].sum(axis=0)
        if self.lin_w is not None:
            out = out + np.nan_to_num(X) @ self.lin_w
        return out

    @classmethod
    def combine(cls, a: TreeEnsemble, b: TreeEnsemble, lam: float = 1.0) -> TreeEnsemble:
        """One ensemble computing a(X) + lam * b(X) (trees concatenated, b's leaf values scaled)."""
        n = max(a.feature.shape[1], b.feature.shape[1])

        def cat(f: str, fill: float, scale: float = 1.0) -> np.ndarray:
            xa, xb = getattr(a, f), getattr(b, f)
            pa = np.full((xa.shape[0], n), fill, dtype=xa.dtype)
            pb = np.full((xb.shape[0], n), fill, dtype=xb.dtype)
            pa[:, : xa.shape[1]] = xa
            pb[:, : xb.shape[1]] = xb * scale if f == "value" else xb
            return np.concatenate([pa, pb])

        if a.lin_w is not None or b.lin_w is not None:
            raise ValueError("combine: linear terms not supported")
        return cls(baseline=a.baseline + lam * b.baseline, feature=cat("feature", 0), threshold=cat("threshold", 0.0),
                   left=cat("left", 0), right=cat("right", 0), missing_left=cat("missing_left", False),
                   is_leaf=cat("is_leaf", True), value=cat("value", 0.0, lam), depth=max(a.depth, b.depth))

    def arrays(self, prefix: str) -> dict[str, np.ndarray]:
        lin = {} if self.lin_w is None else {f"{prefix}__lin_w": self.lin_w}
        return {f"{prefix}__{k}": getattr(self, k) for k in
                ("feature", "threshold", "left", "right", "missing_left", "is_leaf", "value")} | {
            f"{prefix}__baseline": np.array(self.baseline), f"{prefix}__depth": np.array(self.depth)} | lin

    @classmethod
    def from_arrays(cls, z: dict, prefix: str) -> TreeEnsemble:
        g = {k: z[f"{prefix}__{k}"] for k in ("feature", "threshold", "left", "right", "missing_left", "is_leaf", "value")}
        lw = z.get(f"{prefix}__lin_w")
        return cls(baseline=float(z[f"{prefix}__baseline"]), depth=int(z[f"{prefix}__depth"]), lin_w=lw, **g)


class ValueModel:
    def __init__(self, ensembles: dict[str, TreeEnsemble], feature_names: list[str], card: dict) -> None:
        self.ensembles = ensembles
        self.feature_names = feature_names
        self.card = card

    @property
    def version(self) -> str:
        return str(self.card.get("model_version", "?"))

    def predict(self, X: np.ndarray) -> dict[str, np.ndarray]:
        X = np.atleast_2d(X)
        out = {k: e.predict(X) for k, e in self.ensembles.items()}
        for k in ("p_playoffs", "p_title"):
            if k in out:
                out[k] = np.clip(out[k], 0.0, 1.0)
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        arrs: dict[str, np.ndarray] = {}
        for k, e in self.ensembles.items():
            arrs |= e.arrays(k)
        arrs["feature_names"] = np.array(self.feature_names)
        np.savez_compressed(path, **arrs)
        path.with_suffix(".card.json").write_text(json.dumps(self.card, indent=2) + "\n")

    @classmethod
    def load(cls, path: Path | str | None = None) -> ValueModel:
        p = Path(path) if path else default_model_path()
        if not p.exists():
            raise FileNotFoundError(f"value model artifact not found: {p} (train with fantasy.valuesearch.train)")
        z = dict(np.load(p, allow_pickle=False))
        targets = sorted({k.split("__")[0] for k in z if "__" in k})
        card_p = p.with_suffix(".card.json")
        card = json.loads(card_p.read_text()) if card_p.exists() else {}
        return cls({t: TreeEnsemble.from_arrays(z, t) for t in targets}, [str(x) for x in z["feature_names"]], card)


HGB_PARAMS = {"max_iter": 400, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 40,
              "l2_regularization": 1.0, "early_stopping": False}


def fit_hgb(X: np.ndarray, y: np.ndarray, seed: int = 0, sample_weight: np.ndarray | None = None,
            **kw: object):  # noqa: ANN201
    from sklearn.ensemble import HistGradientBoostingRegressor
    from threadpoolctl import threadpool_limits

    params = HGB_PARAMS | {"random_state": seed} | kw
    with threadpool_limits(1):  # OpenMP oversubscription made training ~20x slower on a busy box
        return HistGradientBoostingRegressor(**params).fit(X, y, sample_weight=sample_weight)
