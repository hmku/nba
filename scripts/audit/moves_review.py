#!/usr/bin/env python
"""Rival-moves retrospective for league 44495 (read-only).

For every add / waiver claim / trade by the OTHER managers between --start and --end it measures, from that manager's
side, the player in vs the player out:
  ex-ante : our projection committed before the move day (git history of projections_2026_27.inseason.csv),
  ex-post : ESPN box scores on the dates the player was on that team (lineup capture <= 19:15 ET of the game day),
plus pre-move signals (last-5 minutes vs projection, teammate absences, games left this fantasy week, unprojected,
category fit), our top FA alternatives that day, a large-n FA-pool test of the same signals, and an activity profile
(moves vs reconstructed standings / active games).

Value unit = 9-cat z per game (bias_audit_2026-11-09 definition: counting stats / SD of the top-156 projected pool,
FG/FT as makes above pool rate x attempts / SD, TOV negative), centred on the pool's 156th player ("replacement").
VORP total over a window = sum over games played of (z_game - z_repl); a missed game = 0 (= a replacement-level hole is
NOT credited; open-spot adds compare against an empty spot = 0).

Usage: python moves_review.py --start 2026-10-24 --end 2026-11-08 [--repo PATH] [--out DIR] [--exclude-team 11]
Writes moves.csv, signals_pool.csv, activity.csv, sim_candidates.json and summary.json in --out; prints a summary.
Writes nothing in the repo; reads git history with `git show`.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import subprocess
import warnings
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)

ET = ZoneInfo("America/New_York")
PROJ = "data/processed/projections_2026_27.inseason.csv"
CNT = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]
CATS9 = ["fg", "ft", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
INACTIVE_SLOTS = {"BN", "IL", "IL+", None, ""}
LINEUP_CUTOFF_ET = (19, 15)  # capture used for a game day = latest one at or before 19:15 ET that day
POOL_N = 156  # 12 teams x 13 active-ish spots


# ----------------------------------------------------------------------------------------------- io helpers
def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def et_of(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)


def load_snapshots(repo: Path) -> list[dict]:
    snaps = []
    for f in sorted(glob.glob(str(repo / "data/raw/yahoo/league_rosters_2026-*.json"))):
        if ".prev" in f:
            continue
        d = json.load(open(f))
        if "teams" not in d or "captured_at" not in d:
            continue
        teams = {}
        for yid, t in d["teams"].items():
            pl = {p["player_id"]: (p.get("slot"), p.get("status")) for p in t.get("players", []) if p.get("player_id")}
            teams[int(yid)] = {"name": t.get("name"), "draft_slot": t.get("draft_slot"), "players": pl,
                               "yahoo_ids": {p["player_id"]: p.get("yahoo_player_id") for p in t.get("players", [])
                                             if p.get("player_id")}}
        snaps.append({"file": f, "ts": et_of(d["captured_at"]), "teams": teams})
    snaps.sort(key=lambda s: s["ts"])
    # carry forward a team page that failed to parse (0 players)
    for i in range(1, len(snaps)):
        for yid, t in snaps[i - 1]["teams"].items():
            if yid not in snaps[i]["teams"] or not snaps[i]["teams"][yid]["players"]:
                snaps[i]["teams"][yid] = t
    return snaps


def proj_versions(repo: Path) -> list[tuple[datetime, str]]:
    out = sh(repo, "log", "--format=%H %cI", "--", PROJ).split("\n")
    v = [(datetime.fromisoformat(line.split()[1]).astimezone(ET), line.split()[0]) for line in out if line.strip()]
    return sorted(v)


_proj_cache: dict[str, pd.DataFrame] = {}


def proj_at(repo: Path, versions, day: date, hour: int = 0) -> tuple[pd.DataFrame, str]:
    """Projection committed before `hour`:00 ET of `day` (hour=0: what was known the day before the move;
    hour=12: the same-morning daily-routine build, i.e. what our tools used that day)."""
    cut = datetime(day.year, day.month, day.day, hour, tzinfo=ET)
    cands = [s for t, s in versions if t < cut] or [versions[0][1]]
    sha = cands[-1]
    if sha not in _proj_cache:
        _proj_cache[sha] = pd.read_csv(io.StringIO(sh(repo, "show", f"{sha}:{PROJ}"))).set_index("player_id")
    return _proj_cache[sha], sha[:7]


# ----------------------------------------------------------------------------------------------- value model
class ZModel:
    def __init__(self, proj: pd.DataFrame):
        p = proj[proj.mpg.fillna(0) > 0].copy()
        top = p.sort_values("mpg", ascending=False).head(250)
        for _ in range(2):  # pool = top-156 by z, refit SDs on that pool
            self._fit(top)
            p["z"] = self.z(p)
            top = p.sort_values("z", ascending=False).head(POOL_N)
        self._fit(top)
        p["z"] = self.z(p)
        self.repl = float(p.sort_values("z", ascending=False).z.iloc[POOL_N - 1])

    def _fit(self, top):
        self.pfg = top.fgm.sum() / top.fga.sum()
        self.pft = top.ftm.sum() / top.fta.sum()
        self.sd = {c: top[c].std() for c in CNT}
        self.sd["fg"] = (top.fgm - self.pfg * top.fga).std()
        self.sd["ft"] = (top.ftm - self.pft * top.fta).std()

    def cats(self, df) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        for c in CNT:
            out[c] = df[c] / self.sd[c] * (-1 if c == "tov" else 1)
        out["fg"] = (df.fgm - self.pfg * df.fga) / self.sd["fg"]
        out["ft"] = (df.ftm - self.pft * df.fta) / self.sd["ft"]
        return out[CATS9]

    def z(self, df) -> pd.Series:
        return self.cats(df).sum(axis=1)


# ----------------------------------------------------------------------------------------------- moves
def detect_moves(snaps, start: date, end: date, exclude: set[int]) -> list[dict]:
    ev = []  # raw add/drop events
    for a, b in zip(snaps, snaps[1:], strict=False):
        for yid in b["teams"]:
            old = set(a["teams"].get(yid, {}).get("players", {}))
            new = set(b["teams"][yid]["players"])
            for p in new - old:
                ev.append({"kind": "add", "team": yid, "player": p, "t0": a["ts"], "t1": b["ts"]})
            for p in old - new:
                ev.append({"kind": "drop", "team": yid, "player": p, "t0": a["ts"], "t1": b["ts"]})
    # glitch filter: drop+re-add (or add+drop) of the same player/team within 36 h
    bad = set()
    for i, e in enumerate(ev):
        for j, f in enumerate(ev):
            if j > i and e["team"] == f["team"] and e["player"] == f["player"] and e["kind"] != f["kind"] \
                    and abs((f["t1"] - e["t1"]).total_seconds()) < 36 * 3600 and e["kind"] == "drop":
                bad |= {i, j}
    ev = [e for i, e in enumerate(ev) if i not in bad]
    adds = [e for e in ev if e["kind"] == "add"]
    drops = [e for e in ev if e["kind"] == "drop"]
    used = set()
    moves = []
    # trades: player dropped by A and added by B in the same interval
    trades = defaultdict(lambda: {"in": [], "out": []})
    for e in adds:
        m = [d for d in drops if d["player"] == e["player"] and d["t1"] == e["t1"] and d["team"] != e["team"]]
        if m:
            d = m[0]
            key = (e["t1"], tuple(sorted((e["team"], d["team"]))))
            trades[key]["legs"] = trades[key].get("legs", []) + [(d["team"], e["team"], e["player"])]
            used |= {id(e), id(d)}
    for (t1, pair), tr in trades.items():
        for side in pair:
            got = [p for f, t, p in tr["legs"] if t == side]
            gave = [p for f, t, p in tr["legs"] if f == side]
            moves.append({"team": side, "type": "trade", "player_in": got, "player_out": gave,
                          "t0": None, "t1": t1, "pairing": "trade"})
    # pair remaining adds with drops of the same team in the same interval, else nearest drop within 3 days before
    rem_drops = [d for d in drops if id(d) not in used]
    for e in sorted([e for e in adds if id(e) not in used], key=lambda e: e["t1"]):
        same = [d for d in rem_drops if d["team"] == e["team"] and d["t1"] == e["t1"] and id(d) not in used]
        pairing = "same capture interval"
        if not same:
            same = [d for d in rem_drops if d["team"] == e["team"] and id(d) not in used
                    and timedelta(0) <= e["t1"] - d["t1"] <= timedelta(days=3)]
            pairing = "inferred (drop <= 3 d earlier)"
        out = []
        if same:
            out = [same[0]["player"]]
            used.add(id(same[0]))
        else:
            pairing = "open spot (no drop found)"
        moves.append({"team": e["team"], "type": "add", "player_in": [e["player"]], "player_out": out,
                      "t0": e["t0"], "t1": e["t1"], "pairing": pairing})
    moves = [m for m in moves if start <= m["t1"].date() <= end and m["team"] not in exclude]
    return sorted(moves, key=lambda m: m["t1"])


def lineup_for_day(snaps, day: date):
    cut = datetime(day.year, day.month, day.day, *LINEUP_CUTOFF_ET, tzinfo=ET)
    best = None
    for s in snaps:
        if s["ts"] <= cut:
            best = s
    return best


# ----------------------------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--asof", default=None, help="last box-score date used (default: newest in file)")
    ap.add_argument("--repo", default="/Users/harrisonku/dev/nba")
    ap.add_argument("--out", default=str(Path(__file__).parent / "out"))
    ap.add_argument("--exclude-team", default="11", help="comma list of Yahoo team ids to skip (default: us)")
    ap.add_argument("--fa-top", type=int, default=3)
    a = ap.parse_args()
    repo, out = Path(a.repo), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    exclude = {int(x) for x in a.exclude_team.split(",") if x}

    snaps = load_snapshots(repo)
    versions = proj_versions(repo)
    box = pd.read_csv(repo / "data/raw/espn_boxscores/player_games_2027.csv.gz")
    box = box[box.regular_season.astype(bool)].copy()
    box["d"] = pd.to_datetime(box.game_date).dt.date
    asof = date.fromisoformat(a.asof) if a.asof else box.d.max()
    box = box[box.d <= asof]
    played = box[box.played.astype(bool)].copy()
    team_dates = box.groupby("team").d.apply(lambda s: sorted(set(s))).to_dict()
    sched = json.load(open(repo / "data/manual/league_schedule.json"))
    weeks = [(w["week"], date.fromisoformat(w["start"]), date.fromisoformat(w["end"]), w.get("matchups"))
             for w in sched["weeks"]]
    nba = pd.read_csv(repo / "data/raw/bbref_schedule/nba_2027_games.csv")
    nba["d"] = pd.to_datetime(nba.date).dt.date
    nba_games = defaultdict(set)
    for r in nba.itertuples():
        nba_games[r.visitor].add(r.d)
        nba_games[r.home].add(r.d)
    csv_moves = pd.read_csv(repo / "data/processed/league_moves_2026_27.csv")
    latest = snaps[-1]["teams"]
    names = {y: t["name"] for y, t in latest.items()}
    y2slot = {y: t["draft_slot"] for y, t in latest.items()}

    zref = ZModel(proj_at(repo, versions, start)[0])  # one fixed value scale for the whole window
    zc = zref.cats(played).sum(axis=1)
    played["z"] = zc
    played["v"] = played.z - zref.repl
    played = played.set_index(["player_id", "d"]).sort_index()
    played_keys = set(played.index)
    by_pid = {pid: g.droplevel(0) for pid, g in played.groupby(level=0)}
    pteam = box.sort_values("d").groupby("player_id").team.last().to_dict()

    def week_of(d):
        return next((w for w in weeks if w[1] <= d <= w[2]), None)

    def player_team(pid, proj):
        return pteam.get(pid.removeprefix("unknown:")) or (proj.team.get(pid) if pid in proj.index else None)

    def rostered_dates(yid, pid, d0):
        ds = []
        d = d0
        while d <= asof:
            s = lineup_for_day(snaps, d)
            if s and pid in s["teams"].get(yid, {}).get("players", {}):
                ds.append((d, s["teams"][yid]["players"][pid][0]))
            elif ds:  # stint ended
                break
            d += timedelta(days=1)
        return ds

    def expost(pid, days, active_days=None):
        g = by_pid.get(pid.removeprefix("unknown:"))
        rows = g[g.index.isin(days)] if g is not None else pd.DataFrame()
        res = {"gp": len(rows), "min": float(rows["min"].mean()) if len(rows) else np.nan,
               "zpg": float(rows.z.mean()) if len(rows) else np.nan, "vorp": float(rows.v.sum()) if len(rows) else 0.0}
        if active_days is not None:
            ra = rows[rows.index.isin(active_days)] if len(rows) else rows
            res["gp_active"] = len(ra)
            res["vorp_active"] = float(ra.v.sum()) if len(ra) else 0.0
        return res

    def exante(pid, proj, days):
        if pid not in proj.index:
            return {"proj": False, "zpg": np.nan, "mpg": np.nan, "share": np.nan, "vorp": np.nan}
        r = proj.loc[[pid]]
        z = float(zref.z(r).iloc[0])
        share = r.ros_share.iloc[0] if "ros_share" in r and pd.notna(r.ros_share.iloc[0]) else r.gp.iloc[0] / 82
        tm = player_team(pid, proj)
        tg = len([d for d in days if d in nba_games.get(tm, set())])
        return {"proj": True, "zpg": z, "mpg": float(r.mpg.iloc[0]), "share": float(share),
                "vorp": float(tg * share * (z - zref.repl)), "team_games": tg}

    def pre_signals(pid, d0, proj, yid, snap_before):
        """Signals visible before the move day d0."""
        tm = player_team(pid, proj)
        tdates = [d for d in team_dates.get(tm, []) if d < d0][-5:]
        g = by_pid.get(pid.removeprefix("unknown:"), pd.DataFrame())
        last = g[g.index.isin(tdates)] if len(g) else g
        ea = exante(pid, proj, [])
        s = {"l5_team_games": len(tdates), "l5_gp": len(last),
             "l5_min": float(last["min"].mean()) if len(last) else np.nan,  # per game played
             "l5_zpg": float(last.z.mean()) if len(last) else np.nan}
        s["min_gap"] = s["l5_min"] - ea["mpg"] if ea["proj"] else np.nan
        s["z_gap"] = s["l5_zpg"] - ea["zpg"] if ea["proj"] else np.nan
        # teammates with proj mpg >= 20 who missed both of the team's last 2 games
        last2 = tdates[-2:]
        mates = proj[(proj.team == tm) & (proj.mpg >= 20)].index.difference([pid])
        prev3 = tdates[:-2]  # NEW absences only: missed the last 2 team games but played one of the 3 before
        missing = [m for m in mates if len(last2) == 2 and not any((m, d) in played_keys for d in last2)
                   and any((m, d) in played_keys for d in prev3)]
        s["role_open_min"] = float(proj.loc[missing, "mpg"].sum()) if missing else 0.0
        s["role_open_who"] = ",".join(missing)
        w = week_of(d0)
        if w:
            span = [w[1] + timedelta(days=i) for i in range((w[2] - w[1]).days + 1) if w[1] + timedelta(days=i) >= d0]
            left = {t: len([d for d in span if d in nba_games[t]]) for t in nba_games}
            s["week_games_left"] = left.get(tm, np.nan)
            s["week_games_vs_avg"] = left.get(tm, np.nan) - float(np.mean(list(left.values())))
        s["unprojected"] = not ea["proj"]
        # category fit: player's top-3 cats vs team's weakest-3 (league rank of projected roster totals)
        if ea["proj"] and snap_before:
            tot = {}
            for y, t in snap_before["teams"].items():
                ids = [p for p in t["players"] if p in proj.index]
                c = zref.cats(proj.loc[ids]).mul(proj.loc[ids].ros_share.fillna(proj.loc[ids].gp / 82), axis=0)
                tot[y] = c.sum()
            tot = pd.DataFrame(tot).T
            weak = tot.rank(ascending=True).loc[yid].sort_values().index[:3].tolist() if yid in tot.index else []
            mine = zref.cats(proj.loc[[pid]]).iloc[0].sort_values(ascending=False).index[:3].tolist()
            s["cat_fit"] = len(set(weak) & set(mine))
            s["team_weak3"] = ",".join(weak)
            s["player_top3"] = ",".join(mine)
        return s

    rows = []
    sim_cands = []
    for m in detect_moves(snaps, start, end, exclude):
        yid = m["team"]
        d_move = m["t1"].date()
        proj, sha = proj_at(repo, versions, d_move if m["t1"].hour >= 7 else d_move)  # committed before move day
        snap_before = max((s for s in snaps if s["ts"] < m["t1"]), key=lambda s: s["ts"], default=None)
        if m["t0"] is not None:
            snap_before = max((s for s in snaps if s["ts"] <= m["t0"]), key=lambda s: s["ts"], default=snap_before)
        # CSV pairing / verdict override
        csvrow = None
        for pin in m["player_in"]:
            c = csv_moves[(csv_moves.player_in.astype(str).str.contains(pin, regex=False))]
            c = c[(pd.to_datetime(c.date).dt.date - d_move).abs() <= timedelta(days=2)]
            if len(c):
                csvrow = c.iloc[0]
        pout = list(m["player_out"])
        pairing = m["pairing"]
        if csvrow is not None and m["type"] != "trade" and isinstance(csvrow.player_out, str):
            pout = csvrow.player_out.split("|")
            pairing = "league_moves csv"
        elif csvrow is not None and m["type"] != "trade" and not isinstance(csvrow.player_out, str):
            pout, pairing = [], "league_moves csv (open spot)"
        first_day = min((d for d in [d_move, d_move + timedelta(days=1)]
                         if all(p in (lineup_for_day(snaps, d) or {"teams": {}})["teams"].get(yid, {}).get("players", {})
                                for p in m["player_in"])), default=d_move + timedelta(days=1))
        stints = {p: rostered_dates(yid, p, first_day) for p in m["player_in"]}
        days = sorted({d for st in stints.values() for d, _ in st})
        if not days:
            days = [d for d in (first_day + timedelta(days=i) for i in range((asof - first_day).days + 1))]
        act = {p: [d for d, sl in st if sl not in INACTIVE_SLOTS] for p, st in stints.items()}
        ein = [expost(p, [d for d, _ in stints[p]] or days, act[p]) for p in m["player_in"]]
        eout = [expost(p, days) for p in pout]
        ain = [exante(p, proj, [d for d, _ in stints[p]] or days) for p in m["player_in"]]
        aout = [exante(p, proj, days) for p in pout]
        sig = pre_signals(m["player_in"][0], d_move, proj, yid, snap_before)
        # our top FA alternatives by ex-ante value over the same days (not rostered before the move)
        rostered = {p for t in (snap_before or snaps[0])["teams"].values() for p in t["players"]}
        fa = proj[~proj.index.isin(rostered) & ~proj.status.fillna("").str.lower().isin(["out", "out_season", "o"])].copy()
        fa["z"] = zref.z(fa)
        fa["share"] = fa.ros_share.fillna(fa.gp / 82)
        fa["v_exante"] = fa.z.sub(zref.repl).mul(fa.share)  # per team-game
        topfa = fa.sort_values("v_exante", ascending=False).head(a.fa_top).index.tolist()
        alt = [expost(p, days) for p in topfa]
        tool, tool_file = [], None
        for f in sorted(glob.glob(str(repo / "data/processed/verdicts/stream_2026-*.json"))):
            if Path(f).name[7:17] <= (d_move - timedelta(days=0)).isoformat():
                tool_file = f
        try:
            for pr_ in (json.load(open(tool_file)).get("pairs", []) if tool_file else []):
                if pr_["add"] not in tool and pr_["add"] not in rostered:
                    tool.append(pr_["add"])
                if len(tool) == a.fa_top:
                    break
        except (OSError, ValueError, KeyError):
            pass
        talt = [expost(p, days) for p in tool]
        # FA pool residual over the same days (players with >=1 game): average selection-free residual
        pool_ids = fa.sort_values("v_exante", ascending=False).head(40).index
        pres = []
        for p in pool_ids:
            e = expost(p, days)
            if e["gp"]:
                pres.append(e["zpg"] - fa.z[p])
        rin = [e["zpg"] - x["zpg"] for e, x in zip(ein, ain, strict=False) if x["proj"] and e["gp"]]
        sumv = lambda xs, k: float(np.nansum([x.get(k, 0) or 0 for x in xs]))  # noqa: E731
        row = {
            "date": d_move.isoformat(), "time_et": m["t1"].strftime("%m-%d %H:%M"), "yahoo_team": yid,
            "slot": y2slot.get(yid), "team": names.get(yid), "type": m["type"], "pairing": pairing,
            "player_in": "|".join(m["player_in"]), "player_out": "|".join(pout), "proj_sha": sha,
            "first_day": first_day.isoformat(), "last_day": days[-1].isoformat() if days else "",
            "days": len(days),
            "in_proj": all(x["proj"] for x in ain), "in_proj_zpg": np.nanmean([x["zpg"] for x in ain]) if ain else np.nan,
            "in_proj_mpg": np.nanmean([x["mpg"] for x in ain]) if ain else np.nan,
            "in_gp": sum(e["gp"] for e in ein), "in_gp_active": sum(e["gp_active"] for e in ein),
            "in_min": np.nanmean([e["min"] for e in ein]), "in_zpg": np.nanmean([e["zpg"] for e in ein]),
            "out_proj_zpg": np.nanmean([x["zpg"] for x in aout]) if aout else np.nan,
            "out_gp": sum(e["gp"] for e in eout), "out_zpg": np.nanmean([e["zpg"] for e in eout]) if eout else np.nan,
            "exante_delta": sumv(ain, "vorp") - sumv(aout, "vorp"),
            "expost_delta": sumv(ein, "vorp") - sumv(eout, "vorp"),
            "expost_delta_active": sumv(ein, "vorp_active") - sumv(eout, "vorp"),
            "resid_in_zpg": float(np.mean(rin)) if rin else np.nan,
            "fa_pool_resid_zpg": float(np.mean(pres)) if pres else np.nan,
            "topfa": "|".join(topfa), "topfa_vorp_mean": float(np.mean([e["vorp"] for e in alt])) if alt else np.nan,
            "in_vorp": sumv(ein, "vorp"),
            "tool_top": "|".join(tool), "tool_top_vorp_mean": float(np.mean([e["vorp"] for e in talt])) if talt else np.nan,
            "csv_class": csvrow["class"] if csvrow is not None else "", "csv_verdict": csvrow.our_prior_verdict if
            csvrow is not None else "", "csv_delta_us": csvrow.delta if csvrow is not None else np.nan,
        }
        row.update({f"sig_{k}": v for k, v in sig.items()})
        # SE of ex-post delta: per-game z SD (fitted below) x sqrt(games)
        rows.append(row)
        sim_cands.append({"date": d_move.isoformat(), "slot": y2slot.get(yid), "yahoo_team": yid, "type": m["type"],
                          "add": m["player_in"], "drop": pout, "proj_sha": sha,
                          "premove_snapshot": snap_before["file"] if snap_before else None})
    mv = pd.DataFrame(rows)
    # within-player game-to-game SD of z (noise scale for ex-post SE)
    gsd = float(played.groupby(level=0).z.std().dropna().median())
    if len(mv):
        mv["expost_se"] = gsd * np.sqrt(mv.in_gp + mv.out_gp)
        ea, ep = mv.exante_delta, mv.expost_delta
        mv["class"] = np.select([(ea > 0) & (ep > 0), (ea <= 0) & (ep > 0), (ea > 0) & (ep <= 0)],
                                ["good", "lucky", "unlucky"], "bad")
        mv.loc[mv.exante_delta.isna(), "class"] = np.where(mv.expost_delta > 0, "good?(unproj)", "bad?(unproj)")
        mv["expost_sig"] = (mv.expost_delta.abs() > 2 * mv.expost_se)
        mv.loc[(mv.in_gp + mv.out_gp) == 0, "class"] = "n/a (no games yet)"
    mv.to_csv(out / "moves.csv", index=False)
    json.dump(sim_cands, open(out / "sim_candidates.json", "w"), indent=1, default=str)

    # ------------------------------------------------ large-n FA-pool signal test (daily, all projected players)
    pool_rows = []
    for d0 in (start + timedelta(days=i) for i in range((min(end, asof - timedelta(days=3)) - start).days + 1)):
        proj, _ = proj_at(repo, versions, d0, hour=12)  # same-morning build = what our tools saw on d0
        s0 = lineup_for_day(snaps, d0)
        ros = {p for t in s0["teams"].values() for p in t["players"]} if s0 else set()
        pr = proj[(proj.mpg.fillna(0) >= 8)].copy()
        if "mpg_next5" in pr:  # short-horizon minutes when the build has them
            pr["mpg"] = pr.mpg_next5.fillna(pr.mpg)
        pr["z"] = zref.z(pr)
        nxt = [d0 + timedelta(days=i) for i in range(7)]
        for pid, r in pr.iterrows():
            tm = r.team
            tdates = [d for d in team_dates.get(tm, []) if d < d0][-5:]
            ndates = [d for d in team_dates.get(tm, []) if d in nxt]
            if len(tdates) < 3 or not ndates:
                continue
            g = by_pid.get(pid)
            if g is None:
                continue
            last = g[g.index.isin(tdates)]
            fut = g[g.index.isin(ndates)]
            if len(last) < 2 or not len(fut):
                continue
            last2 = tdates[-2:]
            mates = pr[(pr.team == tm) & (pr.mpg >= 20)].index.difference([pid])
            prev3 = tdates[:-2]
            missing = [m for m in mates if not any((m, d) in played_keys for d in last2)
                       and any((m, d) in played_keys for d in prev3)]
            pool_rows.append({
                "d": d0, "player_id": pid, "rostered": pid in ros, "proj_mpg": r.mpg, "proj_z": r.z,
                "l5_min": last["min"].mean(), "l5_avail": len(last) / len(tdates), "l5_zpg": last.z.mean() if len(last) else np.nan,
                "role_open_min": float(pr.loc[missing, "mpg"].sum()) if missing else 0.0,
                "n_min": fut["min"].mean(), "n_zpg": fut.z.mean() if len(fut) else np.nan,
                "n_gp": len(fut), "n_tg": len(ndates)})
    pool = pd.DataFrame(pool_rows)
    pool.to_csv(out / "signals_pool.csv", index=False)

    def clustered_ols(df, y, xs):
        df = df.dropna(subset=[y, *xs])
        X = np.column_stack([np.ones(len(df)), *[df[x] for x in xs]])
        Y = df[y].to_numpy()
        b = np.linalg.lstsq(X, Y, rcond=None)[0]
        e = Y - X @ b
        XtXi = np.linalg.inv(X.T @ X)
        meat = np.zeros((X.shape[1], X.shape[1]))
        for _, idx in df.groupby("player_id").indices.items():
            sc = X[idx].T @ e[idx]
            meat += np.outer(sc, sc)
        se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
        return {"n": len(df), "players": df.player_id.nunique(),
                **{x: {"b": round(float(b[i + 1]), 3), "se": round(float(se[i + 1]), 3)} for i, x in enumerate(xs)}}

    tests = {}
    if len(pool):
        pool["min_gap"] = pool.l5_min - pool.proj_mpg
        pool["n_min_resid"] = pool.n_min - pool.proj_mpg
        pool["z_gap"] = pool.l5_zpg - pool.proj_z
        pool["n_z_resid"] = pool.n_zpg - pool.proj_z
        fa_pool = pool[~pool.rostered]
        tests = {
            "all: next-7d min resid ~ last-5 min gap": clustered_ols(pool, "n_min_resid", ["min_gap"]),
            "FA: next-7d min resid ~ last-5 min gap": clustered_ols(fa_pool, "n_min_resid", ["min_gap"]),
            "FA: next-7d z/g resid ~ last-5 z gap": clustered_ols(fa_pool, "n_z_resid", ["z_gap"]),
            "FA: next-7d min resid ~ teammate absent min": clustered_ols(fa_pool, "n_min_resid", ["role_open_min"]),
            "FA: next-7d min resid ~ both": clustered_ols(fa_pool, "n_min_resid", ["min_gap", "role_open_min"]),
            "all: mean next-7d min resid": {"mean": round(float(pool.n_min_resid.mean()), 2)},
            "FA: mean next-7d z resid": {"mean": round(float(fa_pool.n_z_resid.mean()), 3)},
        }

    # ------------------------------------------------ activity profile + reconstructed standings
    act = []
    team_week = defaultdict(lambda: defaultdict(float))
    games_tw = defaultdict(int)
    cats_raw = ["fgm", "fga", "ftm", "fta", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]
    bx = box[box.played.astype(bool)]
    for d in sorted(set(bx.d)):
        s = lineup_for_day(snaps, d)
        w = week_of(d)
        if not s or not w:
            continue
        day = bx[bx.d == d].set_index("player_id")
        for yid, t in s["teams"].items():
            on = [p for p, (sl, _) in t["players"].items() if sl not in INACTIVE_SLOTS and p in day.index]
            if not on:
                continue
            sub = day.loc[on]
            for c in cats_raw:
                team_week[(yid, w[0])][c] += float(sub[c].sum())
            games_tw[(yid, w[0])] += len(on)
    slot2y = {v: k for k, v in y2slot.items()}
    rec = defaultdict(lambda: [0, 0, 0, 0, 0])  # W L T catW catL
    for wk, _ws, we, mts in weeks:
        if we > asof or not mts:
            continue
        for sa, sb in mts:
            ya, yb = slot2y.get(sa), slot2y.get(sb)
            ta, tb = team_week[(ya, wk)], team_week[(yb, wk)]
            cw = cl = 0
            for c in ["fg", "ft", "fg3m", "pts", "reb", "ast", "stl", "blk", "tov"]:
                if c in ("fg", "ft"):
                    x = ta[c + "m"] / ta[c + "a"] if ta[c + "a"] else 0
                    y = tb[c + "m"] / tb[c + "a"] if tb[c + "a"] else 0
                else:
                    x, y = ta[c], tb[c]
                s_ = np.sign(x - y) * (-1 if c == "tov" else 1)
                cw += s_ > 0
                cl += s_ < 0
            for y_, w_, l_ in ((ya, cw, cl), (yb, cl, cw)):
                r = rec[y_]
                r[0 if w_ > l_ else (1 if w_ < l_ else 2)] += 1
                r[3] += w_
                r[4] += l_
    nweeks = len([w for w in weeks if w[2] <= asof])
    mvc = mv.groupby("yahoo_team").size() if len(mv) else pd.Series(dtype=int)
    for y in sorted(names):
        r = rec[y]
        act.append({"yahoo_team": y, "slot": y2slot.get(y), "team": names[y], "moves": int(mvc.get(y, 0)),
                    "W": r[0], "L": r[1], "T": r[2], "catW": r[3], "catL": r[4],
                    "active_games_per_week": round(np.mean([games_tw[(y, w[0])] for w in weeks if w[2] <= asof]), 1)
                    if nweeks else np.nan,
                    "sum_expost_delta": round(float(mv[mv.yahoo_team == y].expost_delta.sum()), 1) if len(mv) else 0})
    act = pd.DataFrame(act)
    act.to_csv(out / "activity.csv", index=False)

    # ------------------------------------------------ summary
    def mse(x):
        x = pd.Series(x).dropna()
        return {"mean": round(float(x.mean()), 3) if len(x) else None,
                "se": round(float(x.std(ddof=1) / np.sqrt(len(x))), 3) if len(x) > 1 else None, "n": int(len(x))}

    adds = mv[mv.type == "add"] if len(mv) else mv
    summ = {
        "window": [a.start, a.end], "asof": asof.isoformat(), "n_moves": int(len(mv)),
        "value_scale": {"repl_zpg": round(zref.repl, 3), "game_z_sd": round(gsd, 2)},
        "class_counts": mv["class"].value_counts().to_dict() if len(mv) else {},
        "adds_resid_zpg (ex-post - our ex-ante, per game)": mse(adds.resid_in_zpg) if len(adds) else {},
        "fa_pool_resid_zpg (top-40 FA by our ex-ante, same days)": mse(adds.fa_pool_resid_zpg) if len(adds) else {},
        "selection_edge_zpg (adds resid - FA pool resid)": mse(adds.resid_in_zpg - adds.fa_pool_resid_zpg)
        if len(adds) else {},
        "pick_vs_our_topFA_vorp (added player - mean of our top-3 FA, same days)":
            mse(adds.in_vorp - adds.topfa_vorp_mean) if len(adds) else {},
        "pick_vs_stream_tool_top_vorp (added player - mean of top-3 adds in our stream file that day)":
            mse(adds.in_vorp - adds.tool_top_vorp_mean) if len(adds) else {},
        "expost_delta (in - out, VORP)": mse(mv.expost_delta) if len(mv) else {},
        "exante_delta (in - out, VORP)": mse(mv.exante_delta) if len(mv) else {},
        "signal_tests_pool": tests,
        "activity_corr": {
            "moves~catW-catL (spearman)": round(float(act.moves.corr(act.catW - act.catL, method="spearman")), 2),
            "moves~active_games (spearman)": round(float(act.moves.corr(act.active_games_per_week,
                                                                        method="spearman")), 2)} if len(act) else {},
    }
    # signals among adds vs outcome
    if len(adds) > 4:
        sc = {}
        for s_ in ["sig_min_gap", "sig_z_gap", "sig_role_open_min", "sig_week_games_vs_avg", "sig_cat_fit"]:
            if s_ in adds:
                sc[s_] = {"rho_with_resid": round(float(adds[s_].corr(adds.resid_in_zpg, method="spearman")), 2),
                          "rho_with_pick_vs_topFA": round(float(adds[s_].corr(adds.in_vorp - adds.topfa_vorp_mean,
                                                                                method="spearman")), 2),
                          "n": int(adds[[s_, "resid_in_zpg"]].dropna().shape[0]), "mean": round(float(adds[s_].mean()), 2)}
        summ["signals_among_adds"] = sc
    json.dump(summ, open(out / "summary.json", "w"), indent=1, default=str)
    print(json.dumps(summ, indent=1, default=str))


if __name__ == "__main__":
    main()
