"""Vectorised Monte Carlo season simulator for a Yahoo H2H-categories league.

One simulation = one season. Shapes: N sims, T fantasy teams, R roster rows per team (padded),
W fantasy weeks, D fantasy days, S raw stats.

Per player and sim (random streams keyed by (seed, player id) so roster changes keep common random
numbers for everyone else):
1. Season-level projection error (prior, uncalibrated): a volume factor exp(N(0, vol_sd)) on all
   counting stats and attempts, a per-stat factor exp(N(0, stat_sd)) on FGA, FTA, 3PM, REB, AST,
   STL, BLK, TO, and additive shifts on FG% (sd fg_pct_sd) and FT% (sd ft_pct_sd). Makes =
   attempts x shifted percentage; PTS = 2*FGM + 3PM + FTM, rescaled to the projected PTS.
2. Availability: games-played fraction f ~ Beta with mean gp/82 and sd gp_sd/82. The missed share
   1-f is split into injury *spells* (share 1 - rest_share: one spell, or two with probability
   two_spell_prob, placed uniformly without overlap on the NBA-season timeline; players listed
   out/out_for_season start the season in a spell) and scattered one-game rests (share rest_share,
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
5. Weekly team totals: given the counted games k, team attempts/counting stats ~ Normal with mean
   sum(k * mean) and variance sum(k * (GAME_CV * mean)^2) (noise drawn at team level, keyed by team
   id); makes | attempts ~ Normal(p_team * A, binomial + mix variance); counts are rounded to integers
   and percentages to 3 decimals, so ties happen (ties count 0.5).
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
from fantasy.model.lines import GAME_CV, SEASON_GAMES, STATS
from fantasy.sim.league import LeagueState
from fantasy.sim.schedule import FantasyWeek, MatchupSchedule, NBASchedule

SI = {s: i for i, s in enumerate(STATS)}
FACTOR_STATS = ["fga", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov"]  # get their own season factor
NOISE_STATS = ["fga", "fgm", "fta", "ftm", "fg3m", "reb", "ast", "stl", "blk", "tov"]


@dataclass
class SimParams:
    n_sims: int = 2000
    seed: int = 0
    # season-level projection error (priors; calibrate with T-010 backtest residuals)
    vol_sd: float = 0.08
    stat_sd: float = 0.08
    fg_pct_sd: float = 0.015
    ft_pct_sd: float = 0.03
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

    # ------------------------------------------------------------------ player draws
    def _player_arrays(self, pids: list[str]) -> dict[str, np.ndarray]:
        """Per-player season means with season-level error (N, P, S), health (N, P+1, D)."""
        N, P, D = self.p.n_sims, len(pids), len(self.fdays)
        df = self.proj
        mu = np.zeros((N, P, len(STATS)), dtype=np.float64)
        health = np.zeros((N, P + 1, D), dtype=bool)  # last row = padding (never plays)
        team_rows = np.full(P + 1, -1, dtype=int)
        fi = [SI[s] for s in FACTOR_STATS]
        t_mid = self.fday_offset + 0.5
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
            vol = np.exp(rng.normal(0, self.p.vol_sd, N))
            fac = np.exp(rng.normal(0, self.p.stat_sd, (N, len(fi))))
            fg_shift = rng.normal(0, self.p.fg_pct_sd, N)
            ft_shift = rng.normal(0, self.p.ft_pct_sd, N)
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
            out_now = status in ("out", "out_for_season")
            q = 1.0 - f  # missed share
            rest = 0.0 if out_now else self.p.rest_share
            spell_len = (1 - rest) * q * self.season_days
            h = 1 - (1 - rest) * q
            r_iid = np.clip(1 - (1 - q) / np.maximum(h, 1e-9), 0, 1)
            two = (rng.random(N) < self.p.two_spell_prob) & (not out_now)
            u = np.where(two, rng.uniform(0.2, 0.8, N), 1.0)
            L1, L2 = spell_len * u, spell_len * (1 - u)
            free = np.maximum(self.season_days - spell_len, 0)
            x, y = rng.random(N) * free, rng.random(N) * free
            a1 = np.where(out_now, 0.0, np.minimum(x, y))
            a2 = np.maximum(x, y) + L1
            in_spell = ((t_mid[None, :] >= a1[:, None]) & (t_mid[None, :] < (a1 + L1)[:, None])) | (
                (t_mid[None, :] >= a2[:, None]) & (t_mid[None, :] < (a2 + L2)[:, None]))
            rest_draw = rng.integers(0, 256, (N, D), dtype=np.uint8)
            healthy = ~in_spell & (rest_draw >= np.round(r_iid * 256)[:, None])
            if self.p.use_playoff_mult and pmult < 1.0 and self.po_days.any():
                # late-season rest/tanking: extra one-game absences in fantasy playoff weeks
                extra = rng.integers(0, 256, (N, int(self.po_days.sum())), dtype=np.uint8)
                healthy[:, self.po_days] &= extra >= round((1.0 - pmult) * 256)
            health[:, j, :] = healthy & self.plays[team_rows[j]][None, :]
        return {"mu": mu, "health": health}

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
        arr = self._player_arrays(pids)
        mu = np.concatenate([arr["mu"], np.zeros((N, 1, len(STATS)))], axis=1)  # pad row
        health = arr["health"]
        muTR = mu[:, idx, :]  # (N, T, R, S)
        cv = np.array([GAME_CV[s] for s in STATS])
        rep = (self.replacement[STATS].to_numpy(dtype=float) if self.replacement is not None
               else np.zeros(len(STATS)))

        W = len(self.calendar)
        C = len(self.cats)
        totals = np.zeros((N, W, T, C), dtype=np.float32)
        games = np.zeros((N, W, T), dtype=np.float32)
        streams = np.zeros((N, W, T), dtype=np.float32)
        team_rngs = [np.random.default_rng([self.p.seed, 7_000_003, int(t)]) for t in team_ids]
        noise_i = {s: k for k, s in enumerate(NOISE_STATS)}
        for wi, (d0, d1) in enumerate(self.week_slices):
            H = health[:, :, d0:d1][:, idx, :]  # (N, T, R, d)
            counted = H & (np.cumsum(H, axis=2, dtype=np.int16) <= self.active)
            k = counted.sum(axis=3).astype(np.float64)  # (N, T, R)
            n_play = counted.sum(axis=2)  # (N, T, d) starters used per day
            any_game = self.plays[:-1, d0:d1].any(axis=0)  # days with NBA games
            open_slots = ((self.active - n_play) * any_game[None, None, :]).sum(axis=2)
            g_s = np.minimum(self.stream_cap, open_slots).astype(np.float64)  # (N, T)
            E = np.einsum("ntr,ntrs->nts", k, muTR) + g_s[..., None] * rep
            V = np.einsum("ntr,ntrs->nts", k, (muTR * cv) ** 2) + g_s[..., None] * (rep * cv) ** 2
            Z = np.stack([rng.standard_normal((N, len(NOISE_STATS))) for rng in team_rngs], axis=1)  # (N,T,10)
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
                ca = GAME_CV[den] * a_p
                binv = np.einsum("ntr,ntr->nt", k, a_p * pp * (1 - pp))
                mix = np.einsum("ntr,ntr->nt", k, ca**2 * (pp - p_team[..., None]) ** 2)
                prep = rep[SI[num]] / rep[SI[den]] if rep[SI[den]] > 0 else 0.0
                binv = binv + g_s * rep[SI[den]] * prep * (1 - prep)
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
                        totals[:, wi, :, ci] = np.where(vals[c.den] > 0, np.round(vals[c.num] / np.maximum(vals[c.den], 1), 3), 0.0)
                else:
                    totals[:, wi, :, ci] = vals[c.stat]
            games[:, wi] = k.sum(axis=2) + g_s
            streams[:, wi] = g_s

        res = self._score(totals, games, streams, team_ids, warnings)
        res.seconds = time.perf_counter() - t0
        return res

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
