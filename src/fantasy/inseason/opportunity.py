"""Minutes-opportunity detector (opportunity-*): free agents whose minutes are about to jump.

See docs/research/opportunity_detector.md (method, fitted numbers, backtest). Two signals per player:

(a) **minutes jump already observed**: model A (`mpg_next5`, minutes-0.2.0, next-5-games EWMA regression) minus his
    preseason projected mpg >= `jump_min` (PRIOR threshold). Reported with his last-3 mpg and starts.
(b) **vacated minutes**: a teammate projected >= `star_mpg` (28) mpg is Out (ESPN injury report "Out", Yahoo O/INJ
    tag, or a high/medium injury news item). FITTED (2024-25 + 2025-26 box scores, scripts/opportunity/fit.py):
    teammates absorb `share` (~0.77, placebo-corrected) of the star's minutes; a teammate's share depends on his depth rank (by
    projected mpg among available teammates) and on whether he shares a position group (G = PG/SG, W = SF,
    B = PF/C) with the star. The weights are the EXCESS gain over placebo windows (no absence) of the same depth
    bucket, so regression to the mean of the ranking is removed. Allocation never exceeds `share` x star mpg. For
    free-agent-like players the allocation is then scaled by `vacated_calib` (0.95, FITTED on the 2024-25 backtest,
    confirmed out of sample on 2025-26).
    Expected absence length (games still to miss) from the FITTED survival of >= 28-mpg absences given the games
    already missed, capped at `horizon` (PRIOR) games; out-for-season -> `horizon`.

Opportunity minutes: `mpg_next5` plus the (time-averaged, calibrated) minutes of absences model A has NOT seen yet,
capped at `mpg_cap`. An absence that has lasted >= `observed_games` team games is observed: if he played them model A
carries the jump (signal (a)); if he sat while healthy the coach has shown he is not getting those minutes.
Per-minute rates are kept (FITTED: per-minute rates of big gainers during absences / rest of season ~1.00-1.04).
The opportunity line holds over the expected window (absence games, or 5 games for a jump with no injury driver);
afterwards the minutes-model level (`mpg`, model B) returns (no retention credit: conservative; see the note).

Override projections (same schema as projections_2026_27.inseason.csv, only candidate rows changed):
* `<out>.window.csv`: per-game counting stats scaled to the opportunity minutes (the line DURING the window);
  `mpg` = `mpg_next5` = opportunity mpg. For lineup / week tools over the window.
* `<out>.csv` (season): rest-of-season blend for the season simulator, which has one line per player:
  mpg_season = (W x opp + (R - W) x mpg) / R with W = expected window games played (window x gp/82) and R =
  `ros_games`; counting stats scaled by mpg_season / mpg; `mpg_next5` = opportunity mpg. Percentages are unchanged
  because makes and attempts scale together.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

OPPORTUNITY_VERSION = "opportunity-0.1.1"  # 0.1.1: stream-chain season check, week window cut, leak-free calibration
COUNT_STATS = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
GROUPS = ("G", "W", "B")

# FITTED (scripts/opportunity/fit.py -> scripts/opportunity/report_fit.json, 2024-25 + 2025-26 regular seasons):
# S(k) = P(absence >= k games | >= 1 missed) for players averaging >= 28 mpg over their last <= 10 games played,
# k = 1..40; 1174 spells. P(>= 5 | >= 3) 0.58, P(>= 10 | >= 3) 0.25, P(>= 20 | >= 3) 0.085. Spells cut by the season end
# count at their observed length (slightly short tail).
ABSENCE_SURVIVAL: tuple[float, ...] = (
    1.0, 0.4055, 0.27, 0.1942, 0.1567, 0.1269, 0.1082, 0.0869, 0.075, 0.0664, 0.0545, 0.0486, 0.046, 0.0417, 0.0392,
    0.0349, 0.0315, 0.0298, 0.0273, 0.023, 0.0179, 0.017, 0.0145, 0.0128, 0.0111, 0.0094, 0.0094, 0.006, 0.0043, 0.0043,
    0.0043, 0.0034, 0.0034, 0.0034, 0.0026, 0.0026, 0.0026, 0.0026, 0.0017, 0.0017)
# Excess share of the star's minutes per teammate (event gain minus placebo gain of the same depth bucket, / star mpg),
# by depth bucket (rank by projected mpg among available teammates: 1-3, 4-5, 6-8, 9-11, 12+) and position match.
# Position = the player's PRIMARY position (bbref's single position in the fit; the first Yahoo position here), from the
# prior season's bbref data in the fit (no later-season data). Floored at 0 (the top-3 bucket LOSES ~0.05-0.08 x star
# mpg). 317 absences of >= 3 games, 3934 teammate rows; SEs 0.008-0.020 (iid) on the cells, placebo bucket means within
# +-0.6 mpg (SE 0.3-0.8). "unknown" (no position; n <= 21 in the fit, unstable) uses the "other" weight. Keys:
# (bucket, match).
SHARE_WEIGHTS: dict[tuple[int, str], float] = {
    (0, "same"): 0.0, (0, "other"): 0.0, (0, "unknown"): 0.0,
    (1, "same"): 0.0858, (1, "other"): 0.0279, (1, "unknown"): 0.0279,
    (2, "same"): 0.0992, (2, "other"): 0.0783, (2, "unknown"): 0.0783,
    (3, "same"): 0.1654, (3, "other"): 0.1339, (3, "unknown"): 0.1339,
    (4, "same"): 0.1059, (4, "other"): 0.0740, (4, "unknown"): 0.0740,
}
RANK_EDGES = (3, 5, 8, 11)  # bucket upper bounds (ranks are 1-based); rank > 11 -> last bucket
# FITTED: sum over teammates of the excess gain / star mpg = 0.77, SE 0.034 (raw gain without the placebo
# correction 0.81; the earlier research note's 0.73 kept teammates who were themselves out at the start).
VACATED_SHARE = 0.77
# FITTED (scripts/opportunity/backtest.py, 2024-25 backtest, leakage-free "who is out" = pregame inactive list):
# for the detector's universe (players outside the top 156 by preseason value) the realised extra minutes while the
# teammate stays out = 0.95 (SE 0.15) x the allocated extra (slope through the origin). Out of sample on 2025-26 with
# this calibration the slope was 1.27 (SE 0.20): no evidence of over-allocation. (An earlier backtest that took a
# star "out" when he sat game g, rests included, gave 0.54: that was the leak, not the allocation.)
VACATED_CALIB = 0.95
# Value calibration (FITTED, same backtest): realised / predicted window value gain over the model-B line for VACATED
# first flags (bias-corrected with the unflagged pool's per-game residual; G-scores are linear in the line, so window
# totals add up) = 1.14 (SE 0.39) on 2024-25 and 1.06 (SE 0.41) out of sample on 2025-26: not different from 1, so 1.0
# (capped at 1: conservative). For JUMP flags the ratio is ill-conditioned (the predicted uplift over model B is small;
# realised value is higher, SE > 6) and no adjustment is applied: a jump candidate is "model A says his minutes rose".
VALUE_CALIB = 1.0
VALUE_CALIB_NOTE = ("vacated: 1.14 +- 0.39 (2024-25), 1.06 +- 0.41 (2025-26 OOS) -> 1.0 (capped); jump: ill-conditioned, "
                    "not calibrated")


@dataclass(frozen=True)
class OpportunityParams:
    star_mpg: float = 28.0  # definition of a "minutes-vacating" teammate (research: >= 28 mpg)
    share: float = VACATED_SHARE  # FITTED
    horizon: int = 20  # PRIOR: cap on the window (games); beyond it model A/B have learned the new role
    jump_min: float = 4.0  # PRIOR: mpg_next5 - preseason mpg that counts as an observed jump
    vacated_min: float = 2.0  # PRIOR: allocated vacated mpg that counts as a signal
    observed_games: int = 2  # PRIOR: team games into the absence after which it is "observed" (no fresh credit)
    mpg_cap: float = 40.0  # PRIOR: opportunity minutes never exceed this
    jump_window: int = 5  # model A's horizon: window (games) of a jump with no injury driver
    p_out_news: float = 0.5  # PRIOR: P(misses the next game) when only a news item (e.g. "exited the game") says so
    # calibration of the allocation for the detector's universe (free-agent-like players): realised extra minutes during
    # the absence / allocated extra (slope through the origin). FITTED on the 2024-25 backtest, see VACATED_CALIB.
    vacated_calib: float = VACATED_CALIB
    value_calib: float = VALUE_CALIB  # FITTED: scales the per-game value uplift of the opportunity line
    survival: tuple[float, ...] = field(default_factory=lambda: ABSENCE_SURVIVAL)
    weights: Mapping[tuple[int, str], float] = field(default_factory=lambda: dict(SHARE_WEIGHTS))
    rank_edges: tuple[int, ...] = RANK_EDGES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["weights"] = {f"{b}:{s}": round(w, 4) for (b, s), w in self.weights.items()}
        d["survival"] = [round(x, 4) for x in self.survival[:25]]
        return d


@dataclass
class Absence:
    """A minutes-vacating teammate who is Out. `missed` = consecutive team games already missed."""

    player_id: str
    name: str
    team: str
    mpg: float
    missed: int = 0
    season_out: bool = False
    return_date: str | None = None
    espn_games: int | None = None  # team games before ESPN's estimated return date (None: no estimate)
    sources: list[dict] = field(default_factory=list)  # [{source, status, date, detail}]
    p_out: float = 1.0  # probability he is really out (news-only "exited the game" items: OpportunityParams.p_out_news)

    def to_dict(self) -> dict:
        return asdict(self)


def position_groups(positions: object) -> frozenset[str]:
    """{'G','W','B'} groups of a Yahoo position string 'PG|SG' (G = PG/SG, W = SF, B = PF/C; 'G'/'F' generic)."""
    if positions is None or (isinstance(positions, float) and np.isnan(positions)):
        return frozenset()
    text = "|".join(map(str, positions)) if isinstance(positions, list | tuple | set | frozenset) else str(positions)
    text = text.replace(",", "|").replace("/", "|").replace("-", "|")
    s = {p.strip().upper() for p in text.split("|") if p.strip()}
    g = set()
    if s & {"PG", "SG", "G"}:
        g.add("G")
    if s & {"SF", "F"}:
        g.add("W")
    if s & {"PF", "C"}:
        g.add("B")
    return frozenset(g)


def primary_group(positions: object) -> frozenset[str]:
    """Group of the player's first (primary) listed position: the fitted weights use one position per player."""
    if positions is None or (isinstance(positions, float) and np.isnan(positions)):
        return frozenset()
    if isinstance(positions, list | tuple):
        items = [str(x) for x in positions]
    else:
        items = str(positions).replace(",", "|").replace("/", "|").replace("-", "|").split("|")
    items = [x.strip() for x in items if x.strip()]
    return position_groups(items[0]) if items else frozenset()


def rank_bucket(rank: int, edges: Sequence[int] = RANK_EDGES) -> int:
    for i, e in enumerate(edges):
        if rank <= e:
            return i
    return len(edges)


def expected_absence_games(missed: int, params: OpportunityParams | None = None, season_out: bool = False) -> float:
    """Expected further games missed (including the next one, capped at `horizon`) given he is Out now and has
    already missed `missed` consecutive games: sum_{k=m+1}^{m+H} S(k) / S(m+1) (S fitted; beyond its table the
    last ratio is extended geometrically)."""
    p = params or OpportunityParams()
    H = p.horizon
    if season_out:
        return float(H)
    S = list(p.survival)
    if not S:
        return float(min(H, 5))
    while len(S) < missed + H + 1:  # extend: constant hazard of the last 5 table entries
        tail = S[-5:]
        r = (tail[-1] / tail[0]) ** (1 / 4) if tail[0] > 0 and tail[-1] > 0 else 0.0
        S.append(S[-1] * r)
    m = max(0, int(missed))
    base = S[m]  # S index k-1 = P(L >= k); P(L >= m+1)
    if base <= 0:
        return 1.0
    return float(sum(S[k - 1] for k in range(m + 1, m + H + 1)) / base)


def absence_window(a: Absence, params: OpportunityParams | None = None) -> tuple[float, str]:
    """(expected games still to miss, capped at the horizon; its source). Out for the season -> horizon; an ESPN
    estimated return date -> the SHORTER of the team games before it and the fitted survival (PRIOR rule; both reported); else the fitted survival."""
    p = params or OpportunityParams()
    if a.season_out:
        return float(p.horizon), "out for season (horizon cap, prior)"
    fitted = expected_absence_games(a.missed, p)
    if a.espn_games is not None:
        espn = float(min(p.horizon, max(1, a.espn_games)))
        if espn < fitted:
            return espn, (f"ESPN return estimate {a.return_date} ({espn:.0f} g; fitted survival {fitted:.1f} g): the "
                          "shorter one (PRIOR rule)")
        return fitted, (f"fitted survival given {a.missed} missed ({fitted:.1f} g; ESPN return estimate "
                        f"{a.return_date} = {espn:.0f} g): the shorter one (PRIOR rule)")
    return fitted, f"fitted survival given {a.missed} missed"


def allocate_vacated(star_mpg: float, star_groups: frozenset[str],
                     teammates: Sequence[tuple[str, float, frozenset[str]]],
                     params: OpportunityParams | None = None) -> dict[str, float]:
    """Expected extra mpg per available teammate (id, projected mpg, groups) while the star is out.

    weight_i = w(depth bucket of i, position match); extra_i = star_mpg x w_i x min(1, share / sum w). The total is
    therefore <= share x star_mpg (the fitted weights sum to ~share on a typical 13-15-man rotation)."""
    p = params or OpportunityParams()
    order = sorted(teammates, key=lambda t: -float(t[1]))
    w = {}
    for r, (pid, _mpg, g) in enumerate(order, start=1):
        match = "unknown" if not (g and star_groups) else ("same" if g & star_groups else "other")
        w[pid] = max(0.0, float(p.weights.get((rank_bucket(r, p.rank_edges), match), 0.0)))
    tot = sum(w.values())
    if tot <= 0 or star_mpg <= 0:
        return dict.fromkeys(w, 0.0)
    scale = min(1.0, p.share / tot)
    return {pid: float(star_mpg) * x * scale for pid, x in w.items()}


def detect(players: pd.DataFrame, absences: Sequence[Absence], params: OpportunityParams | None = None,
           recent_played: Mapping[str, Sequence[bool]] | None = None, universe: Sequence[str] | None = None) -> list[dict]:
    """Opportunity rows for players in `universe` (default: all) with a signal.

    players: indexed by player_id with columns team, positions, mpg (model B), mpg_next5 (model A), mpg_pre
    (preseason projected mpg) and `out` (bool: the player himself is unavailable). recent_played[pid]: played flags
    of his team's games so far (chronological), used to decide whether model A has already seen the absence."""
    p = params or OpportunityParams()
    recent_played = recent_played or {}
    df = players
    alloc: dict[str, list[dict]] = {}
    for a in absences:
        mates = df[(df["team"] == a.team) & (df.index != a.player_id) & ~df["out"].astype(bool)]
        tm = [(pid, float(r["mpg"]), primary_group(r["positions"])) for pid, r in mates.iterrows()]
        star_g = primary_group(df.loc[a.player_id, "positions"]) if a.player_id in df.index else frozenset()
        ex = allocate_vacated(a.mpg, star_g, tm, p)
        win, src = absence_window(a, p)
        for pid, x in ex.items():
            alloc.setdefault(pid, []).append({"absence": a, "extra_mpg": x * a.p_out * p.vacated_calib, "window_games": win,
                                              "window_source": src})
    pool = list(universe) if universe is not None else list(df.index)
    out = []
    for pid in pool:
        if pid not in df.index or bool(df.loc[pid, "out"]):
            continue
        r = df.loc[pid]
        n5, pre, ros = float(r["mpg_next5"]), float(r["mpg_pre"]), float(r["mpg"])
        d5 = n5 - pre
        jump = d5 >= p.jump_min
        drivers = alloc.get(pid, [])
        vac = sum(dv["extra_mpg"] for dv in drivers)
        played = list(recent_played.get(pid, []))
        # an absence that has already lasted >= observed_games team games is "observed": if he played them, model A
        # carries the jump; if he sat (DNP while healthy), the coach has shown he is not getting those minutes
        observed = [dv["absence"].missed >= p.observed_games for dv in drivers]
        played_in = [sum(bool(x) for x in played[-dv["absence"].missed:]) if dv["absence"].missed else 0
                     for dv in drivers]
        fresh = [dv for dv, seen in zip(drivers, observed, strict=True) if not seen and dv["extra_mpg"] > 0]
        seen_dv = [dv for dv, seen in zip(drivers, observed, strict=True) if seen and dv["extra_mpg"] > 0]
        unobserved_vac = sum(dv["extra_mpg"] for dv in fresh)
        # an absence model A has already seen counts only through the observed jump (signal a); a fresh one must
        # put him >= vacated_min above both his current level and his preseason mpg
        vacated = unobserved_vac >= p.vacated_min and n5 + unobserved_vac - pre >= p.vacated_min
        if not (jump or vacated):
            continue
        wins = [dv["window_games"] for dv in fresh] if vacated else []
        if jump:
            wins += [dv["window_games"] for dv in seen_dv] or [float(p.jump_window)]
        window = max(wins)
        # time-averaged extra minutes over the window (a short absence adds its minutes for its own games only)
        extra = sum(dv["extra_mpg"] * min(1.0, dv["window_games"] / window) for dv in fresh) if vacated else 0.0
        opp = min(p.mpg_cap, n5 + extra)
        kind = ("vacated+jump" if jump else "vacated") if vacated else ("jump (absence)" if seen_dv else "jump")
        out.append({
            "player_id": pid, "team": str(r["team"]), "positions": str(r["positions"]), "signal": kind,
            "mpg_pre": round(pre, 2), "mpg_ros": round(ros, 2), "mpg_next5": round(n5, 2), "d_next5_vs_pre": round(d5, 2),
            "jump": bool(jump), "vacated_mpg": round(vac, 2), "vacated_unobserved_mpg": round(unobserved_vac, 2),
            "opp_mpg": round(opp, 2), "window_games": round(float(window), 2), "after_mpg": round(ros, 2),
            "drivers": [{"player_id": dv["absence"].player_id, "name": dv["absence"].name,
                         "mpg": round(dv["absence"].mpg, 1), "missed": dv["absence"].missed,
                         "season_out": dv["absence"].season_out, "return_date": dv["absence"].return_date,
                         "sources": dv["absence"].sources, "p_out": dv["absence"].p_out,
                         "extra_mpg": round(dv["extra_mpg"], 2), "window_games": round(dv["window_games"], 2),
                         "window_source": dv["window_source"], "observed": seen, "played_during": pl}
                        for dv, seen, pl in zip(drivers, observed, played_in, strict=True) if dv["extra_mpg"] > 0.05],
        })
    return out


def scaled_lines(df: pd.DataFrame, target_mpg: Mapping[str, float], next5: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Copy of a projections frame (indexed by player_id) whose rows in `target_mpg` have per-game counting stats
    scaled by target / mpg (per-minute rates kept; percentages unchanged) and mpg = target. Other rows untouched."""
    out = df.copy()
    for pid, t in target_mpg.items():
        if pid not in out.index:
            continue
        m = float(out.at[pid, "mpg"])
        if not m > 0:
            continue
        f = float(t) / m
        for c in COUNT_STATS:
            if c in out.columns:
                out.at[pid, c] = round(float(out.at[pid, c]) * f, 4)
        out.at[pid, "mpg"] = round(float(t), 3)
        if next5 is not None and pid in next5 and "mpg_next5" in out.columns:
            out.at[pid, "mpg_next5"] = round(float(next5[pid]), 3)
    return out


def season_mpg(row: pd.Series, opp_mpg: float, window_games: float) -> float:
    """Rest-of-season mpg blend: the opportunity minutes over the expected window games played, model B after."""
    p = min(1.0, max(0.0, float(row.get("gp", 82.0)) / 82.0))
    R = float(row.get("ros_games", np.nan))
    if not R > 0:
        R = 70.0 * p
    W = min(R, window_games * p)
    return float((W * opp_mpg + (R - W) * float(row["mpg"])) / R) if R > 0 else float(row["mpg"])


def override_frames(df: pd.DataFrame, cands: Sequence[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(window frame, season frame) with only the candidates' rows changed (see the module docstring)."""
    opp = {c["player_id"]: float(c["opp_mpg"]) for c in cands if c["player_id"] in df.index}
    window = scaled_lines(df, opp, opp)
    seas = {pid: season_mpg(df.loc[pid], opp[pid], next(float(c["window_games"]) for c in cands
                                                           if c["player_id"] == pid)) for pid in opp}
    return window, scaled_lines(df, seas, opp)


def week_mpg(base_mpg: float, opp_mpg: float, window_games: float, week_games: int) -> float:
    """Average mpg over his `week_games` games this matchup week when the opportunity minutes hold only for the first
    `window_games` of them (model B's level afterwards): the week check must not credit the whole week."""
    if week_games <= 0:
        return float(base_mpg)
    w = min(float(window_games), float(week_games))
    return float((w * opp_mpg + (week_games - w) * base_mpg) / week_games)


def stint_end_day(team: str, eff, window_games: float, games: Sequence[tuple]) -> object:
    """First day AFTER the window: the day after his team's ceil(window_games)-th game on/after `eff` (games =
    (date, visitor, home) rows); if fewer games remain, the day after the last one."""
    from datetime import timedelta

    days = sorted({d for d, v, h in games if d >= eff and team in (v, h)})
    if not days:
        return eff
    k = max(1, int(np.ceil(float(window_games))))
    return days[min(k, len(days)) - 1] + timedelta(days=1)


def calibrated_gain(window_games: float, p_play: float, v_base: float, v_opp: float, p_drop: float, v_drop: float,
                    value_calib: float) -> float:
    """Window value gain vs the drop with the opportunity uplift (v_opp - v_base, per game) scaled by the FITTED
    value calibration: window x (p x (v_base + calib x (v_opp - v_base)) - p_drop x v_drop)."""
    return float(window_games * (p_play * (v_base + value_calib * (v_opp - v_base)) - p_drop * v_drop))
