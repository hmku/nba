"""Vectorised Monte Carlo season simulator for a Yahoo H2H-categories league.

One simulation = one season. Shapes: N sims, T fantasy teams, R roster rows per team (padded),
W fantasy weeks, D fantasy days, S raw stats.

Per player and sim (random streams keyed by (seed, player id) so roster changes keep common random
numbers for everyone else):
1. Season-level projection error (fitted, docs/research/sim_calibration.md): a volume factor
   exp(N(0, vol_sd)) on all counting stats and attempts (vol_sd and the FG%/FT% sds by projected mpg
   tier unless set), a per-stat factor exp(N(0, stat_sds[s])) on FGA,
   FTA, 3PM, REB, AST, STL, BLK, TO (or one `stat_sd` for all), and additive shifts on FG% (sd fg_pct_sd)
   and FT% (sd ft_pct_sd). Makes = attempts x shifted percentage; PTS = 2*FGM + 3PM + FTM, rescaled to the
   projected PTS.
2. Availability: games-played fraction f ~ Beta with mean gp/82 and sd gp_sd/82. The missed share
   1-f is split into injury *spells* (share 1 - rest_share: one spell, or two with probability
   two_spell_prob, without overlap) and placed on the fantasy window treated as a circle (uniform
   rotation, uniform gap; a spell running past the last day continues on day 0). Since season-sim-0.7.0
   this makes P(day in a spell) exactly (1 - rest_share) * (1 - f) on every day and keeps the realised
   window missed share equal to 1 - f (flat availability = gp/82 in every week; before, spell starts
   were min/max of two uniforms on the NBA-season axis: U-shaped, below gp/82). Players listed
   out/out_for_season start the season in their spell (day 0, no wrap) and scattered one-game rests (share rest_share,
   iid per game). A player can therefore miss whole weeks, as in reality. If the projections carry
   `playoff_mult` (< 1: late-season rest/tanking), playoff-week games are additionally missed with
   probability 1 - playoff_mult.
3. Daily lineup: on each day, each fantasy team's healthy players whose NBA team plays are started in
   order of projected value until the `active` (10) starting slots are full; the rest sit. Positional
   eligibility is ignored (2 Util slots make it rarely binding), so usage is slightly optimistic.
   This is a daily cap (stricter than a weekly "10 x days" cap, which almost never binds).
4. Streaming: each team adds up to `stream_games` replacement-level games per week (default
   adds_per_week x games_per_add x stream_usage = 6 x 2 x 0.5 = 6), limited by its open starting
   slot-days that week. Replacement line = valuation's replacement level.
4b. IL replacement (default on since season-sim-0.6.0, `il_replacement`; docs/research/il_replacement.md): while a rostered player is in
   an injury spell of at least `il_min_spell_days` days (proxy for Yahoo's "Out" status, which IL slots
   require), his team carries one replacement-level player in his place, up to `il_slots` at a time. Each
   replacement plays the expected games of a typical NBA team that day (share of the 30 teams playing) times
   `il_rep_availability`, and fills open starting slots after the roster's players and before streamers. No
   extra random numbers are drawn, so runs with and without it stay paired.
4c. Roster cap (default on since season-sim-0.8.0, `roster_cap`; docs/research/il_replacement.md "Roster cap"): a team
   may hold `active_roster` (settings starters + bench = 13) non-IL players plus up to `il_slots` players who are
   Out. On every fantasy day, a rostered player inside an IL-qualifying spell (>= `il_min_spell_days`, or any spell
   of a player who starts the season listed out) occupies an IL slot; if the team's other rostered players exceed
   `active_roster`, its lowest-value ones (same value order as the lineup) that are not Out are released for the
   rest of the season from that day (e.g. 13 + 1 IL: when the IL player's spell ends, the 14th-best goes; if the
   spell runs to the end of the window nobody goes). Released players never come back; later injuries are covered
   by IL replacement. IL replacement only fills an Out player's slot when the team has fewer than `active_roster`
   healthy non-IL players: replacements per day = max(0, min(#Out, il_slots) - (rostered - active_roster)^+).
   Players the league file lists Out on Yahoo (`LeagueState.out_now`) start the season in their spell, like
   projections status out. No extra random numbers. `roster_cap=False` reproduces season-sim-0.7.0 exactly.
5. Weekly team totals: given the counted games k, team attempts/counting stats ~ Normal with mean
   sum(k * mean) and variance sum(k * var(mean)), where the per-game variance is the one game-noise model
   shared with the draft, var = a * mean + b * mean^2 (fantasy.model.noise). Noise is drawn at team level,
   keyed by team id; with `noise_corr` the 10 standard-normal draws are correlated with the fitted
   within-game correlation (sim.calibration.NOISE_CORR; simulator-only). Makes | attempts ~ Normal(p_team * A, binomial + mix variance); counts are
   rounded to integers and percentages to 3 decimals, so ties happen (ties count 0.5).
6. H2H: per week each scheduled pair compares the 9 categories (TO: lower wins). Standings rank by
   category wins + 0.5 * ties (Yahoo H2H categories), random tie-break. Top `playoff_teams` make a
   bracket (top seeds get byes when the bracket is not full); with `settings.playoff_reseed` (league
   44495: yes) the survivors are re-seeded after every round so the top remaining seed plays the
   lowest. A playoff matchup is won by winning more categories; a tie goes to the higher seed
   (`settings.playoff_tiebreak = "higher_seed"`).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from fantasy.league.settings import LeagueSettings
from fantasy.model.lines import SEASON_GAMES, STATS
from fantasy.model.noise import game_var_matrix
from fantasy.sim import calibration as cal
from fantasy.sim.league import LeagueState
from fantasy.sim.schedule import FantasyWeek, MatchupSchedule, NBASchedule

SI = {s: i for i, s in enumerate(STATS)}
FACTOR_STATS = ["fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]  # get their own season factor
NOISE_STATS = ["fga", "fgm", "fta", "ftm", "fg3m", "reb", "ast", "stl", "blk", "tov"]


@dataclass
class SimParams:
    n_sims: int = 2000
    seed: int = 0
    # season-level projection error (fitted out of sample, docs/research/sim_calibration.md; the
    # season-sim-0.3.0 priors were 0.08 / 0.08 / 0.015 / 0.03)
    # None = by the player's projected mpg (cal.ERROR_BY_MPG; cal.VOL_SD etc. if the projections lack mpg)
    vol_sd: float | None = None
    stat_sd: float | None = None  # one sd for all 8 factor stats; None = per-stat `stat_sds`
    stat_sds: dict[str, float] = field(default_factory=lambda: dict(cal.STAT_SD))
    fg_pct_sd: float | None = None
    ft_pct_sd: float | None = None
    # weekly noise: per-game variance from fantasy.model.noise (one model, shared with the draft); this flag
    # only controls the simulator-only within-game correlation across stats (cal.NOISE_CORR)
    noise_corr: bool = True
    # availability (priors)
    rest_share: float = 0.25  # share of missed games that are scattered one-game absences
    two_spell_prob: float = 0.5
    use_playoff_mult: bool = True  # projections' playoff_mult (availability in playoff weeks), if present
    default_gp_sd: float = 12.0
    # lineup / streaming
    active: int | None = None  # starting slots per day (default: settings.starting_slots)
    adds_per_week: int | None = None  # default: settings.max_weekly_adds
    games_per_add: float = 2.0
    stream_usage: float = 0.5  # share of the weekly add budget actually used on streamers
    streaming: bool = True
    # IL replacement (default on since 0.6.0; all PRIORS, docs/research/il_replacement.md): a player in an injury spell of at
    # least il_min_spell_days days is replaced by a replacement-level player for the spell, up to il_slots per team
    il_replacement: bool = True  # default since season-sim-0.6.0 (user-approved 2026-10-20)
    il_slots: int | None = None  # default: settings.roster_slots["IL"] (league 44495: 3)
    il_min_spell_days: float = 7.0
    il_rep_availability: float = 0.85  # share of his team's games the replacement plays (rest/minor injuries)
    # roster cap (season-sim-0.8.0, step 4c): non-IL roster limited to active_roster; surplus released on return
    roster_cap: bool = True
    active_roster: int | None = None  # default: settings.num_rounds (starters + bench; league 44495: 13)
    # playoffs
    playoff_teams: int | None = None  # default: settings.playoff_teams
    playoff_weeks: list[int] | None = None  # default: settings.playoff_weeks
    reseed: bool | None = None  # default: settings.playoff_reseed

    def to_dict(self) -> dict:
        return asdict(self)


def _key(pid: str) -> int:
    return int(hashlib.sha256(pid.encode()).hexdigest()[:12], 16)


def _beta_params(m: np.ndarray, sd: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    m = np.clip(m, 0.01, 0.995)
    v = np.minimum(np.maximum(sd, 1e-3) ** 2, 0.9 * m * (1 - m))
    k = m * (1 - m) / v - 1
    return m * k, (1 - m) * k


def bracket_positions(size: int) -> list[int]:
    """Standard seeding order for a bracket of `size` (power of 2): 8 -> [1,8,4,5,2,7,3,6]."""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


@dataclass
class SimResult:
    params: dict
    team_ids: list[int]
    weeks: list[FantasyWeek]
    regular_weeks: list[int]
    playoff_weeks: list[int]
    cats: list[str]
    totals: np.ndarray  # (N, W, T, C) weekly category values
    games: np.ndarray  # (N, W, T) player-games counted (incl. streamers)
    stream_games: np.ndarray  # (N, W, T)
    cat_wins: np.ndarray  # (N, T) regular-season category wins (ties 0.5)
    cat_losses: np.ndarray  # (N, T)
    rank: np.ndarray  # (N, T) final regular-season standing, 1 = best
    playoff: np.ndarray  # (N, T) bool
    champion: np.ndarray  # (N,) team index
    runner_up: np.ndarray  # (N,)
    matchups: MatchupSchedule
    sources: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    il_games: np.ndarray | None = None  # (N, W, T) IL-replacement games counted (None when il_replacement is off)
    il_slot_days: np.ndarray | None = None  # (N, W, T) IL slots occupied, summed over the week's days
    cap_drop_day: np.ndarray | None = None  # (N, T, R) fantasy-day index a roster row is released (D = never)
    cap_rosters: list[list[str]] | None = None  # roster rows (value order) that cap_drop_day refers to

    @property
    def n(self) -> int:
        return self.totals.shape[0]

    def week_index(self, week: int) -> int:
        return [w.week for w in self.weeks].index(week)

    def tindex(self, team: int) -> int:
        return self.team_ids.index(team)


class SeasonSimulator:
    def __init__(
        self,
        proj: pd.DataFrame,
        values: pd.DataFrame,
        replacement: pd.Series | None,
        settings: LeagueSettings,
        nba: NBASchedule,
        calendar: list[FantasyWeek],
        matchups: MatchupSchedule,
        params: SimParams | None = None,
    ) -> None:
        self.proj = proj
        self.values = values
        self.replacement = replacement
        self.settings = settings
        self.nba = nba
        self.calendar = calendar
        self.matchups = matchups
        self.p = params or SimParams()
        self.cats = settings.cats
        self.active = self.p.active or len(settings.starting_slots)
        adds = self.p.adds_per_week if self.p.adds_per_week is not None else settings.max_weekly_adds
        self.stream_cap = adds * self.p.games_per_add * self.p.stream_usage if self.p.streaming else 0.0
        self.playoff_weeks = list(self.p.playoff_weeks or settings.playoff_weeks)
        self.playoff_teams = int(self.p.playoff_teams or settings.playoff_teams)
        self.reseed = settings.playoff_reseed if self.p.reseed is None else bool(self.p.reseed)
        if settings.playoff_tiebreak != "higher_seed":
            raise NotImplementedError(f"playoff tiebreak {settings.playoff_tiebreak!r}")
        first_po = min(self.playoff_weeks)
        self.regular_weeks = [w.week for w in calendar if w.week < first_po]
        # day axes: NBA-season timeline (for spells) and fantasy days (for lineups)
        self.season_start = min(nba.first_day, calendar[0].start)
        self.season_days = (nba.last_day - self.season_start).days + 1
        self.fdays: list[date] = [d for w in calendar for d in w.days()]
        self.week_slices = []
        k = 0
        for w in calendar:
            n = len(w.days())
            self.week_slices.append((k, k + n))
            k += n
        self.team_row, self.plays = nba.plays_matrix(self.fdays)
        po = set(self.playoff_weeks)
        self.po_days = np.array([w.week in po for w in calendar for _ in w.days()], dtype=bool)
        self.fday_offset = np.array([(d - self.season_start).days for d in self.fdays], dtype=float)
        # injury spells live on the fantasy window (first to last fantasy day), so the missed share q applies
        # to the days that count (season-sim-0.7.0; before, the NBA-season axis + min/max placement made
        # availability U-shaped and ~2-9% below gp/82 in the window)
        self.window_start = float(self.fday_offset[0])
        self.window_days = float(self.fday_offset[-1] - self.fday_offset[0] + 1)
        if self.p.stat_sd is not None:
            self._stat_sd_vec = np.full(len(FACTOR_STATS), float(self.p.stat_sd))
        else:
            self._stat_sd_vec = np.array([float(self.p.stat_sds.get(s, 0.0)) for s in FACTOR_STATS])
        self._chol = np.linalg.cholesky(np.array(cal.NOISE_CORR)) if self.p.noise_corr else None
        self.il_slots = int(self.p.il_slots if self.p.il_slots is not None else settings.roster_slots.get("IL", 0))
        self.active_roster = int(self.p.active_roster if self.p.active_roster is not None else settings.num_rounds)
        self._out_now: set[str] = set()  # league-file Out players (set by run() when the roster cap is on)
        # expected games of a typical NBA team on each fantasy day (share of the NBA teams playing)
        self.team_game_share = self.plays[:-1].mean(axis=0)
        assert cal.NOISE_ORDER == NOISE_STATS

    def error_sds(self, mpg: float) -> tuple[float, float, float]:
        """(vol_sd, fg_pct_sd, ft_pct_sd) for a player with projected minutes `mpg` (nan = unknown)."""
        tier = (cal.VOL_SD, cal.FG_PCT_SD, cal.FT_PCT_SD)
        if np.isfinite(mpg):
            tier = next((t[1:] for t in cal.ERROR_BY_MPG if mpg >= t[0]), cal.ERROR_BY_MPG[-1][1:])
        vals = (self.p.vol_sd, self.p.fg_pct_sd, self.p.ft_pct_sd)
        return tuple(float(v) if v is not None else float(d) for v, d in zip(vals, tier, strict=True))  # type: ignore[return-value]

    def game_variance(self, m: np.ndarray) -> np.ndarray:
        """Per-game variance of each raw stat (last axis = STATS) for season mean m, given the player plays."""
        return game_var_matrix(m)

    # ------------------------------------------------------------------ player draws
    def _player_arrays(self, pids: list[str]) -> dict[str, np.ndarray]:
        """Per-player season means with season-level error (N, P, S), health (N, P+1, D)."""
        N, P, D = self.p.n_sims, len(pids), len(self.fdays)
        df = self.proj
        mu = np.zeros((N, P, len(STATS)), dtype=np.float64)
        health = np.zeros((N, P + 1, D), dtype=bool)  # last row = padding (never plays)
        il_on = self.p.il_replacement or self.p.roster_cap  # spell intervals are needed by both
        cap_on = self.p.roster_cap
        # long-spell intervals [start, end) on the season-day axis, 2 per player (empty: start = end = 0)
        # (each of the 2 spells is at most 2 pieces after wrapping: 4 slots per player)
        il_start = np.zeros((N, P + 1, 4)) if il_on else None
        il_end = np.zeros((N, P + 1, 4)) if il_on else None
        team_rows = np.full(P + 1, -1, dtype=int)
        fi = [SI[s] for s in FACTOR_STATS]
        w0, Tw = self.window_start, self.window_days
        t_w = self.fday_offset - w0 + 0.5  # day midpoints on the fantasy-window axis [0, Tw)
        for j, pid in enumerate(pids):
            rng = np.random.default_rng([self.p.seed, _key(pid)])
            if pid in df.index:
                row = df.loc[pid]
                base = row[STATS].to_numpy(dtype=float)
                gp = float(row["gp"])
                gp_sd = float(row["gp_sd"]) if "gp_sd" in row and pd.notna(row["gp_sd"]) else 0.0
                gp_sd = gp_sd if gp_sd > 0 else self.p.default_gp_sd
                pmult = float(row["playoff_mult"]) if "playoff_mult" in row and pd.notna(row["playoff_mult"]) else 1.0
                team = str(row.get("team") or "")
                status = str(row.get("status") or "")
            else:  # unknown player: replacement line, typical availability
                base = (self.replacement[STATS].to_numpy(dtype=float) if self.replacement is not None
                        else np.zeros(len(STATS)))
                gp, gp_sd, team, status, pmult = 60.0, self.p.default_gp_sd, "", "", 1.0
            team_rows[j] = self.team_row.get(team, len(self.team_row))
            # --- season-level production error
            mpg = float(df.loc[pid, "mpg"]) if pid in df.index and "mpg" in df.columns else float("nan")
            vol_sd, fg_sd, ft_sd = self.error_sds(mpg)
            vol = np.exp(rng.normal(0, vol_sd, N))
            fac = np.exp(rng.normal(0, self._stat_sd_vec, (N, len(fi))))
            fg_shift = rng.normal(0, fg_sd, N)
            ft_shift = rng.normal(0, ft_sd, N)
            m = np.broadcast_to(base, (N, len(STATS))).copy()
            m[:, fi] *= vol[:, None] * fac
            fgp = base[SI["fgm"]] / base[SI["fga"]] if base[SI["fga"]] > 0 else 0.0
            ftp = base[SI["ftm"]] / base[SI["fta"]] if base[SI["fta"]] > 0 else 0.0
            m[:, SI["fgm"]] = m[:, SI["fga"]] * np.clip(fgp + fg_shift, 0.2, 0.8)
            m[:, SI["ftm"]] = m[:, SI["fta"]] * np.clip(ftp + ft_shift, 0.3, 0.98)
            raw_pts = 2 * base[SI["fgm"]] + base[SI["fg3m"]] + base[SI["ftm"]]
            pts_ratio = base[SI["pts"]] / raw_pts if raw_pts > 0 else 1.0
            m[:, SI["pts"]] = (2 * m[:, SI["fgm"]] + m[:, SI["fg3m"]] + m[:, SI["ftm"]]) * pts_ratio
            mu[:, j, :] = m
            # --- availability
            a, b = _beta_params(np.array([gp / SEASON_GAMES]), np.array([gp_sd / SEASON_GAMES]))
            f = rng.beta(a[0], b[0], N)
            out_now = status in ("out", "out_for_season") or pid in self._out_now
            q = 1.0 - f  # missed share
            rest = 0.0 if out_now else self.p.rest_share
            spell_len = (1 - rest) * q * Tw  # total spell days on the fantasy-window circle
            h = 1 - (1 - rest) * q
            r_iid = np.clip(1 - (1 - q) / np.maximum(h, 1e-9), 0, 1)
            # spells on a circle of length T (the fantasy window): uniform rotation c and a uniform gap g
            # between the two spells, so every day is in a spell with probability exactly (1-rest)*q and the
            # in-window missed share is exactly q in every sim (the fitted gp sd is not inflated). Players
            # listed out start the season in their (single) spell: no rotation, no wrap.
            two = (rng.random(N) < self.p.two_spell_prob) & (not out_now)
            u = np.where(two, rng.uniform(0.2, 0.8, N), 1.0)
            L1, L2 = spell_len * u, spell_len * (1 - u)
            c = 0.0 if out_now else rng.random(N) * Tw
            g = rng.random(N) * np.maximum(Tw - spell_len, 0)
            a1, a2 = c, np.mod(c + L1 + g, Tw)
            if il_start is not None and il_end is not None:
                for k_, (st_, ln_) in enumerate(((a1, L1), (a2, L2))):
                    # a player listed out now holds his IL slot for his whole (first) spell, however short (0.8.0)
                    long_ = (ln_ >= self.p.il_min_spell_days) | (cap_on and out_now)
                    st_ = np.broadcast_to(st_, (N,))
                    # pieces on the season-day axis: [st, min(st+L, T)) and the wrapped part [0, st+L-T)
                    il_start[:, j, 2 * k_] = np.where(long_, w0 + st_, 0.0)
                    il_end[:, j, 2 * k_] = np.where(long_, w0 + np.minimum(st_ + ln_, Tw), 0.0)
                    over = np.maximum(st_ + ln_ - Tw, 0.0)
                    il_start[:, j, 2 * k_ + 1] = np.where(long_ & (over > 0), w0, 0.0)
                    il_end[:, j, 2 * k_ + 1] = np.where(long_ & (over > 0), w0 + over, 0.0)
            a1n, a2n = np.broadcast_to(a1, (N,)), a2
            in_spell = (np.mod(t_w[None, :] - a1n[:, None], Tw) < L1[:, None]) | (
                np.mod(t_w[None, :] - a2n[:, None], Tw) < L2[:, None])
            rest_draw = rng.integers(0, 256, (N, D), dtype=np.uint8)
            healthy = ~in_spell & (rest_draw >= np.round(r_iid * 256)[:, None])
            if self.p.use_playoff_mult and pmult < 1.0 and self.po_days.any():
                # late-season rest/tanking: extra one-game absences in fantasy playoff weeks
                extra = rng.integers(0, 256, (N, int(self.po_days.sum())), dtype=np.uint8)
                healthy[:, self.po_days] &= extra >= round((1.0 - pmult) * 256)
            health[:, j, :] = healthy & self.plays[team_rows[j]][None, :]
        out = {"mu": mu, "health": health}
        if il_start is not None and il_end is not None:
            out["il_start"], out["il_end"] = il_start, il_end
        return out

    # ------------------------------------------------------------------ main
    def run(self, league: LeagueState) -> SimResult:
        import time

        t0 = time.perf_counter()
        N = self.p.n_sims
        team_ids = league.team_ids
        T = len(team_ids)
        warnings: list[str] = []
        # roster rows ordered by projected value (lineup priority)
        val = self.values["value"] if "value" in self.values else pd.Series(dtype=float)
        rosters = []
        for t in team_ids:
            ps = list(dict.fromkeys(league.rosters[t]))
            unknown = [p for p in ps if p not in self.proj.index]
            if unknown:
                warnings.append(f"team {t}: {len(unknown)} player(s) not in projections, simulated at replacement level: "
                                + ", ".join(unknown[:5]))
            ps.sort(key=lambda p: -float(val.get(p, -99.0)))
            rosters.append(ps)
        pids = list(dict.fromkeys(p for ps in rosters for p in ps))
        pidx = {p: i for i, p in enumerate(pids)}
        R = max(1, max(len(ps) for ps in rosters))
        pad = len(pids)
        idx = np.full((T, R), pad, dtype=int)
        for ti, ps in enumerate(rosters):
            idx[ti, : len(ps)] = [pidx[p] for p in ps]
        cap_on = self.p.roster_cap
        self._out_now = set(league.out_now) if cap_on else set()
        try:
            arr = self._player_arrays(pids)
        finally:
            self._out_now = set()
        D = len(self.fdays)
        member = self._membership(pids)  # (P+1, D) on the roster that day (None: always)
        memTR = (np.ones((pad + 1, D), dtype=bool) if member is None else member.copy())
        memTR[pad] = False
        memTR = memTR[idx]  # (T, R, D)
        drop_day = self._apply_roster_cap(arr, idx, memTR) if cap_on else None
        self._finalize_arrays(arr, pids)
        mu = np.concatenate([arr["mu"], np.zeros((N, 1, len(STATS)))], axis=1)  # pad row
        health = arr["health"]
        muTR = mu[:, idx, :]  # (N, T, R, S)
        var_g = self.game_variance(muTR)  # (N, T, R, S) per-game variance
        a_idx = {"fga": SI["fga"], "fta": SI["fta"]}
        rep = (self.replacement[STATS].to_numpy(dtype=float) if self.replacement is not None
               else np.zeros(len(STATS)))

        W = len(self.calendar)
        C = len(self.cats)
        totals = np.zeros((N, W, T, C), dtype=np.float32)
        games = np.zeros((N, W, T), dtype=np.float32)
        streams = np.zeros((N, W, T), dtype=np.float32)
        il_on = self.p.il_replacement
        day_ix = np.arange(D)
        il_games = np.zeros((N, W, T), dtype=np.float32) if il_on else None
        il_slot_days = np.zeros((N, W, T), dtype=np.float32) if il_on else None
        if il_on:
            il_s, il_e = arr["il_start"][:, idx], arr["il_end"][:, idx]  # (N, T, R, 4)
        team_rngs = [np.random.default_rng([self.p.seed, 7_000_003, int(t)]) for t in team_ids]
        noise_i = {s: k for k, s in enumerate(NOISE_STATS)}
        for wi, (d0, d1) in enumerate(self.week_slices):
            H = health[:, :, d0:d1][:, idx, :]  # (N, T, R, d)
            counted = H & (np.cumsum(H, axis=2, dtype=np.int16) <= self.active)
            k = counted.sum(axis=3).astype(np.float64)  # (N, T, R)
            n_play = counted.sum(axis=2)  # (N, T, d) starters used per day
            any_game = np.asarray(self.plays[:-1, d0:d1].any(axis=0))  # days with NBA games (asarray: typing only)
            open_day = (self.active - n_play) * any_game[None, None, :]  # (N, T, d)
            if il_games is not None and il_slot_days is not None:
                tm = self.fday_offset[d0:d1] + 0.5
                in_il = ((tm >= il_s[..., None]) & (tm < il_e[..., None])).any(axis=3)  # (N, T, R, d)
                n_il = np.minimum(in_il.sum(axis=2), self.il_slots)  # (N, T, d)
                n_rep = n_il
                if drop_day is not None:
                    # a team carrying more than active_roster players already holds its replacement(s) itself
                    alive = (day_ix[None, None, None, d0:d1] < drop_day[..., None]) & memTR[None, :, :, d0:d1]
                    surplus = np.maximum(alive.sum(axis=2) - self.active_roster, 0)  # (N, T, d)
                    n_rep = np.maximum(n_il - surplus, 0)
                per_rep = self.team_game_share[d0:d1] * self.p.il_rep_availability
                g_il_day = np.minimum(n_rep * per_rep[None, None, :], open_day)
                g_il = g_il_day.sum(axis=2)
                open_slots = (open_day - g_il_day).sum(axis=2)
                il_games[:, wi] = g_il
                il_slot_days[:, wi] = n_il.sum(axis=2)
                g_s = np.minimum(self.stream_cap, open_slots).astype(np.float64)  # (N, T)
                g_r = g_s + g_il  # replacement-level games: streamers + IL replacements
            else:
                open_slots = open_day.sum(axis=2)
                g_s = np.minimum(self.stream_cap, open_slots).astype(np.float64)  # (N, T)
                g_r = g_s
            E = np.einsum("ntr,ntrs->nts", k, muTR) + g_r[..., None] * rep
            V = np.einsum("ntr,ntrs->nts", k, var_g) + g_r[..., None] * self.game_variance(rep)
            Z = np.stack([rng.standard_normal((N, len(NOISE_STATS))) for rng in team_rngs], axis=1)  # (N,T,10)
            if self._chol is not None:
                Z = Z @ self._chol.T
            vals: dict[str, np.ndarray] = {}
            for s in ("fg3m", "reb", "ast", "stl", "blk", "tov"):
                vals[s] = np.maximum(0.0, E[..., SI[s]] + np.sqrt(V[..., SI[s]]) * Z[..., noise_i[s]])
            for num, den in (("fgm", "fga"), ("ftm", "fta")):
                EA, EM = E[..., SI[den]], E[..., SI[num]]
                A = np.maximum(0.0, EA + np.sqrt(V[..., SI[den]]) * Z[..., noise_i[den]])
                p_team = np.where(EA > 0, EM / np.maximum(EA, 1e-9), 0.0)
                a_p = muTR[..., SI[den]]
                with np.errstate(divide="ignore", invalid="ignore"):
                    pp = np.where(a_p > 0, muTR[..., SI[num]] / a_p, 0.0)
                binv = np.einsum("ntr,ntr->nt", k, a_p * pp * (1 - pp))
                mix = np.einsum("ntr,ntr->nt", k, var_g[..., a_idx[den]] * (pp - p_team[..., None]) ** 2)
                prep = rep[SI[num]] / rep[SI[den]] if rep[SI[den]] > 0 else 0.0
                binv = binv + g_r * rep[SI[den]] * prep * (1 - prep)
                scale = np.where(EA > 0, A / np.maximum(EA, 1e-9), 0.0)
                M = p_team * A + np.sqrt(np.maximum(binv + mix, 0) * scale) * Z[..., noise_i[num]]
                vals[den] = np.round(A)
                vals[num] = np.clip(np.round(M), 0, vals[den])
            raw_e = 2 * E[..., SI["fgm"]] + E[..., SI["fg3m"]] + E[..., SI["ftm"]]
            ratio = np.where(raw_e > 0, E[..., SI["pts"]] / np.maximum(raw_e, 1e-9), 1.0)
            for s in ("fg3m", "reb", "ast", "stl", "blk", "tov"):
                vals[s] = np.round(vals[s])
            vals["fg3m"] = np.minimum(vals["fg3m"], vals["fgm"])
            vals["pts"] = np.round((2 * vals["fgm"] + vals["fg3m"] + vals["ftm"]) * ratio)
            for ci, c in enumerate(self.cats):
                if c.kind == "ratio":
                    with np.errstate(divide="ignore", invalid="ignore"):
                        totals[:, wi, :, ci] = np.where(vals[c.den_col] > 0, np.round(vals[c.num_col] / np.maximum(vals[c.den_col], 1), 3), 0.0)
                else:
                    totals[:, wi, :, ci] = vals[c.stat_col]
            games[:, wi] = k.sum(axis=2) + g_r
            streams[:, wi] = g_s

        res = self._score(totals, games, streams, team_ids, warnings)
        res.il_games, res.il_slot_days = il_games, il_slot_days
        if il_on or cap_on:
            res.params["il_slots"] = self.il_slots
        if drop_day is not None:
            res.params["active_roster"] = self.active_roster
            res.cap_drop_day, res.cap_rosters = drop_day, rosters
            over = [t for ti, t in enumerate(team_ids) if memTR[ti].sum(axis=0).max() > self.active_roster]
            if over:
                warnings.append(
                    f"roster cap (season-sim-0.8.0): teams {over} carry more than {self.active_roster} players; the "
                    "surplus (lowest value, not Out) is released when an IL player returns (docs/research/il_replacement.md)")
        res.seconds = time.perf_counter() - t0
        return res

    # ------------------------------------------------------------------ roster cap
    def _membership(self, pids: list[str]) -> np.ndarray | None:
        """(P+1, D) bool: player is on his fantasy roster that day (None = every day). Subclasses with mid-season
        roster changes override it so the roster cap counts only current members."""
        return None

    def _finalize_arrays(self, arr: dict[str, np.ndarray], pids: list[str]) -> None:
        """Hook after the roster cap (subclasses re-solve lineups on the final health)."""

    def _apply_roster_cap(self, arr: dict[str, np.ndarray], idx: np.ndarray, memTR: np.ndarray) -> np.ndarray:
        """Release surplus players (step 4c). Returns drop_day (N, T, R): fantasy-day index from which roster row
        (t, r) is released (D = never). Clears the released players' health and IL intervals from that day on.
        Rows of idx are in value order (best first), so the lowest-value candidates are the last rows."""
        N, (T, R, D) = self.p.n_sims, memTR.shape
        drop_day = np.full((N, T, R), D, dtype=np.int32)
        capped = [ti for ti in range(T) if memTR[ti].sum(axis=0).max() > self.active_roster]
        if not capped:
            return drop_day
        js = idx[capped]  # (Tc, R) player indices (pad rows have empty intervals and are never members)
        s, e = arr["il_start"][:, js], arr["il_end"][:, js]  # (N, Tc, R, 4)
        mem = memTR[capped]  # (Tc, R, D)
        kept = np.ones((N, len(capped), R), dtype=bool)
        dd = np.full((N, len(capped), R), D, dtype=np.int32)
        for d in range(D):
            m = kept & mem[None, :, :, d]
            n = m.sum(axis=2)
            if n.max() <= self.active_roster:
                continue
            tm = self.fday_offset[d] + 0.5
            out = ((tm >= s) & (tm < e)).any(axis=3) & m
            excess = n - np.minimum(out.sum(axis=2), self.il_slots) - self.active_roster  # (N, Tc)
            if not (excess > 0).any():
                continue
            cand = m & ~out
            rank_from_bottom = np.cumsum(cand[..., ::-1], axis=2)[..., ::-1]
            drop = cand & (rank_from_bottom <= excess[..., None])
            kept &= ~drop
            dd[drop] = d
        drop_day[:, capped] = dd
        # released from day dd: no games, no IL slot (intervals clipped to end before that day)
        health = arr["health"]
        cut = np.where(dd < D, self.fday_offset[np.minimum(dd, D - 1)], np.inf)  # (N, Tc, R) season-day offset
        for ci in range(len(capped)):
            for r in range(R):
                j = js[ci, r]
                if j == health.shape[1] - 1:  # pad row
                    continue
                health[:, j, :] &= np.arange(D)[None, :] < dd[:, ci, r, None]
                c = cut[:, ci, r, None]
                arr["il_end"][:, j] = np.minimum(arr["il_end"][:, j], c)
                arr["il_start"][:, j] = np.minimum(arr["il_start"][:, j], arr["il_end"][:, j])
        return drop_day

    # ------------------------------------------------------------------ scoring
    def compare(self, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Category wins/losses of a vs b along the last axis (C). Ties are neither."""
        sign = np.array([1.0 if c.higher_is_better else -1.0 for c in self.cats], dtype=np.float32)
        d = (a - b) * sign
        return (d > 0), (d < 0)

    def _score(self, totals, games, streams, team_ids, warnings) -> SimResult:
        N, W, T, C = totals.shape
        tpos = {t: i for i, t in enumerate(team_ids)}
        week_pos = {w.week: i for i, w in enumerate(self.calendar)}
        wins = np.zeros((N, T))
        losses = np.zeros((N, T))
        for w in self.regular_weeks:
            for a, b in self.matchups.weeks.get(w, []):
                if a not in tpos or b not in tpos:
                    continue
                ia, ib = tpos[a], tpos[b]
                x, y = totals[:, week_pos[w], ia], totals[:, week_pos[w], ib]
                cw, cl = self.compare(x, y)
                nw, nl = cw.sum(axis=1), cl.sum(axis=1)
                ties = C - nw - nl
                wins[:, ia] += nw + 0.5 * ties
                wins[:, ib] += nl + 0.5 * ties
                losses[:, ia] += nl + 0.5 * ties
                losses[:, ib] += nw + 0.5 * ties
        rng = np.random.default_rng([self.p.seed, 99])
        key = wins + rng.random((N, T)) * 1e-3  # random tie-break among equal records
        order = np.argsort(-key, axis=1)  # (N, T) team index by seed
        rank = np.empty_like(order)
        rank[np.arange(N)[:, None], order] = np.arange(1, T + 1)[None, :]
        n_po = min(self.playoff_teams, T)
        playoff = rank <= n_po
        champ, runner = self._playoffs(totals, order[:, :n_po], week_pos)
        return SimResult(
            params={**self.p.to_dict(), "active": self.active, "stream_cap_games_per_week": self.stream_cap,
                    "playoff_teams": n_po, "playoff_weeks": self.playoff_weeks, "reseed": self.reseed},
            team_ids=list(team_ids), weeks=self.calendar, regular_weeks=self.regular_weeks,
            playoff_weeks=self.playoff_weeks, cats=[c.key for c in self.cats], totals=totals, games=games,
            stream_games=streams, cat_wins=wins, cat_losses=losses, rank=rank, playoff=playoff,
            champion=champ, runner_up=runner, matchups=self.matchups,
            sources={"nba_schedule": self.nba.source, "matchups": self.matchups.source}, warnings=warnings,
        )

    def _playoffs(self, totals: np.ndarray, seeds: np.ndarray, week_pos: dict[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """seeds: (N, n) team index by seed. Top seeds get byes; after each round the survivors are
        re-seeded (top remaining seed vs lowest) if `self.reseed`, else the bracket is fixed. Category-count
        ties go to the higher seed. Returns (champion, runner-up)."""
        N, n = seeds.shape
        rounds = max(1, math.ceil(math.log2(max(n, 2))))
        weeks = [w for w in sorted(self.playoff_weeks) if w in week_pos][:rounds]
        if len(weeks) < rounds:
            raise ValueError(f"{n} playoff teams need {rounds} playoff weeks inside the calendar, got {weeks}")
        size = 2**rounds
        pos = bracket_positions(size)
        # slots hold (team index, seed number); seed > n means bye (-1)
        team = np.stack([seeds[:, s - 1] if s <= n else np.full(N, -1) for s in pos], axis=1)
        seed = np.stack([np.full(N, s if s <= n else 10**6) for s in pos], axis=1)
        rows = np.arange(N)
        runner = np.full(N, -1)
        for r, w in enumerate(weeks):
            wi = week_pos[w]
            a_t, b_t = team[:, 0::2], team[:, 1::2]
            a_s, b_s = seed[:, 0::2], seed[:, 1::2]
            ta = totals[rows[:, None], wi, np.maximum(a_t, 0)]  # (N, m, C)
            tb = totals[rows[:, None], wi, np.maximum(b_t, 0)]
            cw, cl = self.compare(ta, tb)
            a_wins = cw.sum(-1) > cl.sum(-1)
            tie = cw.sum(-1) == cl.sum(-1)
            a_adv = np.where(b_t < 0, True, np.where(a_t < 0, False, a_wins | (tie & (a_s < b_s))))
            if r == len(weeks) - 1:
                runner = np.where(a_adv[:, 0], b_t[:, 0], a_t[:, 0])
            team = np.where(a_adv, a_t, b_t)
            seed = np.where(a_adv, a_s, b_s)
            if self.reseed and team.shape[1] > 1:
                srt_t = np.take_along_axis(team, np.argsort(seed, axis=1), 1)
                srt_s = np.take_along_axis(seed, np.argsort(seed, axis=1), 1)
                p = np.array(bracket_positions(team.shape[1])) - 1
                team, seed = srt_t[:, p], srt_s[:, p]
        return team[:, 0], runner
