"""Shared helpers for the end-to-end CLI tests (tests/test_e2e_*.py).

These tests drive ONLY the public `fantasy ...` CLI (typer CliRunner, in-process, or a subprocess)
and assert on the JSON keys / exit codes documented in .claude/skills/live-draft, .claude/skills/agent-run
and docs/runbooks — never on internal module paths — so they keep validating refactors of the CLI.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from typer.testing import CliRunner

from fantasy.cli import app

runner = CliRunner()

N_TEAMS, N_ROUNDS = 12, 13
# league 44495: slot 1 under the third-round-reversal formula
MY_PICKS_3RR_SLOT1 = [1, 24, 36, 37, 60, 61, 84, 85, 108, 109, 132, 133, 156]
VOLATILE_KEYS = {"generated_at", "compute_seconds", "rec_id", "model_run_id", "created_at", "updated_at"}


def rounds_3rr(n: int = N_TEAMS, r: int = N_ROUNDS) -> list[list[int]]:
    """Slot order per round for snake with third-round reversal: F, R, R, F, R, F, R, ..."""
    out = []
    for rnd in range(1, r + 1):
        rev = rnd == 2 or (rnd >= 3 and rnd % 2 == 1)
        out.append(list(range(n, 0, -1)) if rev else list(range(1, n + 1)))
    return out


def rounds_snake(n: int = N_TEAMS, r: int = N_ROUNDS) -> list[list[int]]:
    return [list(range(1, n + 1)) if i % 2 == 0 else list(range(n, 0, -1)) for i in range(r)]


def rounds_arg(rounds: list[list[int]]) -> str:
    return ";".join(",".join(map(str, rr)) for rr in rounds)


def slots_by_overall(rounds: list[list[int]]) -> list[int]:
    return [s for rr in rounds for s in rr]


def cli(*args: object, env: dict[str, str] | None = None) -> tuple[int, object]:
    """Invoke the CLI; with --json in args, parse stdout as JSON (fails loudly if it is not)."""
    res = runner.invoke(app, [str(a) for a in args], env=env)
    out: object = res.stdout
    if "--json" in args:
        try:
            out = json.loads(res.stdout)
        except json.JSONDecodeError as e:  # pragma: no cover - diagnostic
            raise AssertionError(f"non-JSON output (exit {res.exit_code}) for {args}: {res.stdout!r}\n"
                                 f"exception: {res.exception!r}") from e
    return res.exit_code, out


def strip_volatile(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


SLOT_ELIGIBILITY = {
    "PG": {"PG"}, "SG": {"SG"}, "G": {"PG", "SG"}, "SF": {"SF"}, "PF": {"PF"}, "F": {"SF", "PF"},
    "C": {"C"}, "Util": {"PG", "SG", "SF", "PF", "C"}, "UTIL": {"PG", "SG", "SF", "PF", "C"},
}


def lineup_feasible(starting_slots: Iterable[str], positions: list[set[str]]) -> bool:
    """Can every starting slot be filled by a distinct rostered player? (bipartite matching)"""
    slots = list(starting_slots)
    match: dict[int, int] = {}  # player index -> slot index

    def try_slot(si: int, seen: set[int]) -> bool:
        elig = SLOT_ELIGIBILITY[slots[si]]
        for pi, pos in enumerate(positions):
            if pi in seen or not (pos & elig):
                continue
            seen.add(pi)
            if pi not in match or try_slot(match[pi], seen):
                match[pi] = si
                return True
        return False

    return all(try_slot(si, set()) for si in range(len(slots)))
