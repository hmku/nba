"""Deterministic synthetic projections for tests (realistic-ish NBA stat distributions)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

POSITION_SETS = [["PG"], ["PG", "SG"], ["SG"], ["SG", "SF"], ["SF"], ["SF", "PF"], ["PF"], ["PF", "C"], ["C"]]


def make_projections(n: int = 260, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    quality = np.sort(rng.gamma(2.0, 1.0, n))[::-1]
    q = quality / quality.max()
    rows = []
    for i in range(n):
        pos = POSITION_SETS[i % len(POSITION_SETS)]
        big = any(p in ("PF", "C") for p in pos)
        guard = any(p in ("PG", "SG") for p in pos)
        mpg = 14 + 22 * q[i] + rng.normal(0, 2)
        fga = max(2.0, mpg * (0.42 + 0.1 * q[i]) + rng.normal(0, 1))
        fg_pct = np.clip(rng.normal(0.52 if big else 0.45, 0.03), 0.38, 0.68)
        fta = max(0.3, fga * rng.uniform(0.15, 0.45))
        ft_pct = np.clip(rng.normal(0.70 if big else 0.82, 0.06), 0.45, 0.93)
        fg3m = max(0.0, (0.3 if big else 1.6) * mpg / 30 + rng.normal(0, 0.4))
        fgm, ftm = fga * fg_pct, fta * ft_pct
        rows.append({
            "player_id": f"player-{i:03d}", "name": f"Player {i:03d}", "team": "TST",
            "positions": "|".join(pos), "age": 26.0,
            "gp": float(np.clip(rng.normal(68, 10), 20, 82)), "gp_sd": 8.0, "mpg": mpg,
            "fgm": fgm, "fga": fga, "ftm": ftm, "fta": fta, "fg3m": fg3m,
            "pts": 2 * fgm + fg3m + ftm,
            "reb": max(1.0, (9 if big else 4) * mpg / 32 + rng.normal(0, 1)),
            "ast": max(0.5, (6 if guard else 2.2) * mpg / 32 * (0.6 + q[i]) + rng.normal(0, 0.7)),
            "stl": max(0.2, 0.9 * mpg / 32 + rng.normal(0, 0.2)),
            "blk": max(0.05, (1.3 if big else 0.35) * mpg / 32 + rng.normal(0, 0.2)),
            "tov": max(0.3, 0.09 * fga + 0.15 * (6 if guard else 2) * mpg / 32),
            "adp": float(i + 1 + rng.normal(0, 4)) if i < 180 else np.nan,
            "status": "healthy", "injury_note": "", "source": "synthetic",
        })
    return pd.DataFrame(rows)


def write_projections(path: Path, n: int = 260, seed: int = 7) -> Path:
    make_projections(n, seed).to_csv(path, index=False)
    return path
