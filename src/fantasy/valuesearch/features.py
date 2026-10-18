"""Permutation-invariant featurization of a league (T rosters) from each team's point of view.

For team i the feature vector depends on i's own roster and on the *multiset* of opponents' rosters
(only sorted vectors and averages over opponents enter), so permuting opponents leaves it unchanged.

Inputs per team come from the draft category model (`DraftContext.arrays`: effective per-game lines
incl. availability/streaming; `CategoryModel.cat_moments`: weekly category mean and variance) plus raw
availability from projections (gp, gp_sd, playoff_mult, status). Pairwise quantities use the same
Normal approximation as the optimizer, but against each *specific* opponent instead of a
league-average team:

  P[i, j, c] = Phi(sign_c * (mu_ic - mu_jc) / sqrt(var_ic + var_jc)),  M[i, j] = P(i wins matchup vs j)

Features (FEATURE_NAMES, 70):
  cat_p_*      mean_j P[i, j, c]                        (9)  expected category win share vs the field
  ecw          sum_c cat_p                              (1)
  mwp          mean_j M[i, j]                           (1)
  mwp_sorted_k M[i, j] over opponents, descending       (11)
  opp_str_k    opponents' field strength mean_k M[j, k], descending (11)
  rank_str     i's rank by field strength (1 = best)    (1)
  gap_best     mwp - strongest opponent's field strength (1)
  dz_*         sign * (mu_i - league mean) / sqrt(mean var), per category (9)
  zsd_*        sqrt(var_i / mean var) per category      (9)
  gp_sum, gp_sd_rss, po_gp_sum, n_out, opp_gp_mean, opp_po_gp_mean (6)
  value_sum, opp_value_mean, value_top3, n_players, opp_value_max (5)
  min_cat_p, n_cats_above_half, cat_p_sd (3)
  e_matchups_won: sum_j M[i, j] (1), mwp_top3 = mean of the 3 hardest opponents' M (1)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fantasy.draft.optimizer import DraftContext
from fantasy.model.catmodel import CategoryModel
from fantasy.model.lines import SEASON_GAMES

FEATURES_VERSION = "vs-features-1"


def feature_names(cats: list[str], n_teams: int) -> list[str]:
    k = n_teams - 1
    return ([f"cat_p_{c}" for c in cats] + ["ecw", "mwp"] + [f"mwp_sorted_{j}" for j in range(k)]
            + [f"opp_str_{j}" for j in range(k)] + ["rank_str", "gap_best"]
            + [f"dz_{c}" for c in cats] + [f"zsd_{c}" for c in cats]
            + ["gp_sum", "gp_sd_rss", "po_gp_sum", "n_out", "opp_gp_mean", "opp_po_gp_mean"]
            + ["value_sum", "opp_value_mean", "value_top3", "n_players", "opp_value_max"]
            + ["min_cat_p", "n_cats_above_half", "cat_p_sd", "e_matchups_won", "mwp_top3"])


class Featurizer:
    def __init__(self, ctx: DraftContext, proj_df: pd.DataFrame) -> None:
        self.ctx = ctx
        self.model: CategoryModel = ctx.model
        self.a = ctx.arrays
        df = proj_df.reindex(ctx.ids)
        self.gp = df["gp"].fillna(60.0).to_numpy(dtype=float) / SEASON_GAMES
        gsd = df["gp_sd"].fillna(0.0).to_numpy(dtype=float) if "gp_sd" in df else np.zeros(len(df))
        self.gp_var = (np.where(gsd > 0, gsd, 12.0) / SEASON_GAMES) ** 2
        pm = df["playoff_mult"].fillna(1.0).to_numpy(dtype=float) if "playoff_mult" in df else np.ones(len(df))
        self.po_gp = self.gp * pm
        st = df["status"].fillna("").astype(str) if "status" in df else pd.Series([""] * len(df))
        self.out = st.isin(["out", "out_for_season"]).to_numpy(dtype=float)
        self.value = ctx.value
        self.cats = [c.key for c in self.model.cats]
        self.n_teams = ctx.settings.num_teams
        self.names = feature_names(self.cats, self.n_teams)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def pad(leagues: list[list[list[int]]]) -> np.ndarray:
        """leagues[b][t] = roster rows -> (B, T, R) int array padded with -1."""
        B, T = len(leagues), len(leagues[0])
        R = max(1, max(len(r) for lg in leagues for r in lg))
        idx = np.full((B, T, R), -1, dtype=int)
        for b, lg in enumerate(leagues):
            for t, rows in enumerate(lg):
                idx[b, t, : len(rows)] = rows
        return idx

    def _gather(self, arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
        mask = idx >= 0
        safe = np.where(mask, idx, 0)
        g = arr[safe]
        if g.ndim == idx.ndim + 1:
            return (g * mask[..., None]).sum(axis=idx.ndim - 1)
        return (g * mask).sum(axis=-1)

    # ------------------------------------------------------------------ main
    def transform(self, idx: np.ndarray) -> np.ndarray:
        """idx (B, T, R) roster rows (-1 = padding) -> features (B, T, F)."""
        idx = np.asarray(idx)
        B, T, _ = idx.shape
        S, V, Rr = self._gather(self.a.S, idx), self._gather(self.a.V, idx), self._gather(self.a.R, idx)
        mu, var = self.model.cat_moments(S.reshape(B * T, -1), V.reshape(B * T, -1), Rr.reshape(B * T, -1))
        C = mu.shape[1]
        mu, var = mu.reshape(B, T, C), var.reshape(B, T, C)
        sign = self.model.sign
        from scipy.special import ndtr

        z = sign * (mu[:, :, None, :] - mu[:, None, :, :]) / np.sqrt(var[:, :, None, :] + var[:, None, :, :] + 1e-12)
        P = ndtr(z)  # (B, T, T, C)
        M = self.model.matchup_win_prob(P.reshape(-1, C)).reshape(B, T, T)
        eye = np.eye(T, dtype=bool)[None]
        k = T - 1
        Pm = np.where(eye[..., None], 0.0, P)
        cat_p = Pm.sum(axis=2) / k  # (B, T, C)
        Mm = np.where(eye, 0.0, M)
        mwp = Mm.sum(axis=2) / k  # (B, T) field strength
        # opponents' M against me, sorted descending (exclude self)
        Ms = np.where(eye, -np.inf, M)
        mwp_sorted = -np.sort(-Ms, axis=2)[:, :, :k]
        # opponents' field strengths sorted descending
        strength = np.broadcast_to(mwp[:, None, :], (B, T, T))
        opp_str = -np.sort(-np.where(eye, -np.inf, strength), axis=2)[:, :, :k]
        rank_str = 1 + (mwp[:, None, :] > mwp[:, :, None]).sum(axis=2)
        gap_best = mwp - opp_str[:, :, 0]
        lmean = mu.mean(axis=1, keepdims=True)
        lvar = var.mean(axis=1, keepdims=True)
        dz = sign * (mu - lmean) / np.sqrt(lvar + 1e-12)
        zsd = np.sqrt(var / (lvar + 1e-12))
        gp = self._gather(self.gp, idx)
        gp_sd = np.sqrt(self._gather(self.gp_var, idx))
        po = self._gather(self.po_gp, idx)
        n_out = self._gather(self.out, idx)
        val = self._gather(self.value, idx)
        safe = np.where(idx >= 0, idx, 0)
        vals = np.where(idx >= 0, self.value[safe], -np.inf)
        top3 = -np.sort(-vals, axis=2)[:, :, :3]
        top3 = np.where(np.isfinite(top3), top3, 0.0).sum(axis=2)
        n_players = (idx >= 0).sum(axis=2).astype(float)

        def opp_mean(x: np.ndarray) -> np.ndarray:
            return (x.sum(axis=1, keepdims=True) - x) / k

        opp_val_max = np.max(np.where(eye, -np.inf, np.broadcast_to(val[:, None, :], (B, T, T))), axis=2)
        mwp_top3 = np.sort(np.where(eye, np.inf, M), axis=2)[:, :, :3].mean(axis=2)
        feats = [
            cat_p, cat_p.sum(axis=2, keepdims=True), mwp[..., None], mwp_sorted, opp_str, rank_str[..., None].astype(float),
            gap_best[..., None], dz, zsd,
            np.stack([gp, gp_sd, po, n_out, opp_mean(gp), opp_mean(po)], axis=2),
            np.stack([val, opp_mean(val), top3, n_players, opp_val_max], axis=2),
            np.stack([cat_p.min(axis=2), (cat_p > 0.5).sum(axis=2).astype(float), cat_p.std(axis=2),
                      Mm.sum(axis=2), mwp_top3], axis=2),
        ]
        out = np.concatenate(feats, axis=2)
        assert out.shape[2] == len(self.names), (out.shape, len(self.names))
        return out

    def league_rows(self, rosters: dict[int, list[int]]) -> np.ndarray:
        """One league (slot -> rows) -> (T, F) in sorted slot order."""
        lg = [rosters[t] for t in sorted(rosters)]
        return self.transform(self.pad([lg]))[0]
