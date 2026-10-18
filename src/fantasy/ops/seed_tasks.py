"""Initial prioritised backlog. `fantasy task seed` inserts these idempotently (by title)."""

from __future__ import annotations

from typing import Any

from fantasy.ops import tasks

# (priority, area, title, description)
SEED: list[tuple[int, str, str, str]] = [
    (0, "season", "Verify league settings from Yahoo",
     "Confirm categories, roster slots, team count, draft order/type (3RR?), trade/waiver rules "
     "for league 44495 from the Yahoo UI/API; store with source + verified=true."),
    (0, "draft", "Live draft dry-run rehearsal",
     "Full end-to-end mock draft: agent run in dry_run mode, draft:<id> lock, browser reads picks, "
     "`fantasy draft recommend` each pick, decisions logged, no clicks executed."),
    (1, "infra", "Yahoo OAuth setup",
     "Register Yahoo developer app, store client id/secret in env, token refresh flow; read-only "
     "scopes first."),
    (1, "season", "Yahoo roster sync",
     "Pull my roster, all rosters, free agents and transactions from Yahoo into the DB on demand."),
    (1, "season", "Daily lineup optimizer",
     "Pick starters per day given games schedule, injuries and slot eligibility; output lineup "
     "transactions (kind=lineup) for the agent to execute."),
    (1, "season", "Waiver/streaming optimizer",
     "Rank free agents by marginal category value for the current matchup and remaining games; "
     "propose add_drop transactions (require approval)."),
    (1, "model", "Weekly matchup simulation",
     "Monte Carlo of the current H2H matchup per category from projections + games remaining; "
     "win probability per category and overall."),
    (2, "model", "Trade evaluator",
     "Evaluate trade proposals by rest-of-season category value and roster fit; output a "
     "recommendation the agent can present for approval."),
    (2, "draft", "Monte Carlo draft simulation",
     "Simulate opponents' picks from ADP noise to estimate availability and test draft strategy."),
    (2, "model", "Backtesting framework",
     "Replay past seasons to measure projection error and decision quality vs. alternatives."),
    (2, "agent", "Scheduled morning agent",
     "Daily scheduled run: sync roster, check injuries, set lineup, flag waiver opportunities, "
     "write a status summary."),
    (3, "model", "Projection model improvements",
     "Better minutes/usage modelling, injury risk, rookie priors, blend more sources."),
]


def seed() -> dict[str, Any]:
    created, existing = [], []
    for priority, area, title, description in SEED:
        found = tasks.find_by_title(title)
        if found:
            existing.append(found["task_id"])
            continue
        created.append(
            tasks.add(title, description=description, area=area, priority=priority)["task_id"]
        )
    return {"created": created, "existing": existing}
