"""League settings and scoring categories.

Defaults reflect the user's recollection of league 44495 and are marked unverified until
confirmed from Yahoo (API or browser). Code must read settings from here, never hardcode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_FILE = REPO_ROOT / "data" / "manual" / "league_settings.json"


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    yahoo_name: str
    # count: summed per game. ratio: numerator/denominator summed separately (FG%, FT%).
    kind: Literal["count", "ratio"]
    higher_is_better: bool = True
    stat: str | None = None  # projection column for count categories
    num: str | None = None  # projection column for ratio numerator (makes)
    den: str | None = None  # projection column for ratio denominator (attempts)


CATEGORIES: dict[str, Category] = {
    c.key: c
    for c in [
        Category("fg_pct", "FG%", "FG%", "ratio", num="fgm", den="fga"),
        Category("ft_pct", "FT%", "FT%", "ratio", num="ftm", den="fta"),
        Category("fg3m", "3PM", "3PTM", "count", stat="fg3m"),
        Category("pts", "PTS", "PTS", "count", stat="pts"),
        Category("reb", "REB", "REB", "count", stat="reb"),
        Category("ast", "AST", "AST", "count", stat="ast"),
        Category("stl", "STL", "ST", "count", stat="stl"),
        Category("blk", "BLK", "BLK", "count", stat="blk"),
        Category("tov", "TO", "TO", "count", higher_is_better=False, stat="tov"),
    ]
}

NINE_CAT = list(CATEGORIES)

# Which real positions may fill each roster slot.
SLOT_ELIGIBILITY: dict[str, frozenset[str]] = {
    "PG": frozenset({"PG"}),
    "SG": frozenset({"SG"}),
    "G": frozenset({"PG", "SG"}),
    "SF": frozenset({"SF"}),
    "PF": frozenset({"PF"}),
    "F": frozenset({"SF", "PF"}),
    "C": frozenset({"C"}),
    "Util": frozenset({"PG", "SG", "SF", "PF", "C"}),
    "BN": frozenset({"PG", "SG", "SF", "PF", "C"}),
    "IL": frozenset({"PG", "SG", "SF", "PF", "C"}),
}
NON_STARTING = {"BN", "IL", "IL+"}


class LeagueSettings(BaseModel):
    league_key: str = "nba.l.44495"
    league_id: int = 44495
    name: str = "Thomas's Tip-Top League"
    season: str = "2026-27"
    num_teams: int = 12
    scoring_type: Literal["head", "headone", "roto", "points"] = "head"
    categories: list[str] = Field(default_factory=lambda: list(NINE_CAT))
    roster_slots: dict[str, int] = Field(
        default_factory=lambda: {
            "PG": 1, "SG": 1, "G": 1, "SF": 1, "PF": 1, "F": 1, "C": 2, "Util": 2, "BN": 3, "IL": 4,
        }
    )
    draft_order_type: Literal["snake", "snake_3rr"] = "snake_3rr"
    draft_datetime: str = "2026-10-19T22:00:00-04:00"
    seconds_per_pick: int = 60
    my_slot: int = 1
    max_weekly_adds: int = 6
    waiver_days: int = 2
    waiver_type: str = "continual rolling list"
    playoff_teams: int = 6
    playoff_weeks: list[int] = Field(default_factory=lambda: [18, 19, 20])
    source: str = "user_recollection"
    # field name -> True once confirmed against Yahoo
    verified: dict[str, bool] = Field(default_factory=dict)

    @property
    def starting_slots(self) -> list[str]:
        """Expanded list of starting slots, e.g. ['PG','SG','G',...,'C','C','Util','Util']."""
        out: list[str] = []
        for slot, n in self.roster_slots.items():
            if slot not in NON_STARTING:
                out += [slot] * n
        return out

    @property
    def num_rounds(self) -> int:
        """Drafted roster size: starters + bench (IL slots are not drafted)."""
        return sum(n for s, n in self.roster_slots.items() if s not in {"IL", "IL+"})

    @property
    def cats(self) -> list[Category]:
        return [CATEGORIES[k] for k in self.categories]

    def unverified_fields(self) -> list[str]:
        keys = ["num_teams", "scoring_type", "categories", "roster_slots", "draft_order_type", "my_slot"]
        return [k for k in keys if not self.verified.get(k)]


def load_settings(path: Path | None = None) -> LeagueSettings:
    """Load settings: data/manual/league_settings.json overrides defaults when present."""
    p = path or SETTINGS_FILE
    if p.exists():
        return LeagueSettings.model_validate(json.loads(p.read_text()))
    return LeagueSettings()


def save_settings(settings: LeagueSettings, path: Path | None = None) -> Path:
    p = path or SETTINGS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(settings.model_dump_json(indent=2) + "\n")
    return p
