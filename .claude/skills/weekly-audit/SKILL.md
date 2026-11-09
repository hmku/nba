---
name: weekly-audit
description: Weekly model audit + fix + code-cleanup for league 44495 (Mondays, after the daily routine's week review) - league summary and standings, projection bias vs actuals, matchup/category calibration of the simulator, minutes gainers and team rotation shifts, a review of every rival waiver/FA/trade move from the manager's perspective (what do they see that we miss?), then implement the model fixes the evidence supports (backtested, quant-reviewed, re-simmed), review and clean up the week's code, and report. Run by the scheduled task "fantasy-weekly-audit" (Mondays 09:30 ET) or on demand ("run the weekly audit", "check the models for bias"). No Yahoo writes.
---

# Weekly audit (Mondays; repo + read-only data; no Yahoo, no roster actions)

Why: 2026-11-09 the user asked to check the models for bias after week 3. That audit found real errors (team minutes
unconstrained, ~90 players with minutes but no projection, sim streaming volume 2.6x too high) that daily runs never
surface. This skill repeats it weekly, adds a rival-moves review (other managers as a free signal about what our model
misses), and fixes what the evidence supports. First run + method: `docs/research/bias_audit_2026-11-09.md`.

Follow CLAUDE.md (evidence standard, conciseness, learn-from-mistakes) and CLAUDE.local.md (orchestrate: delegate big
pieces to subagents, keep integration/verification yourself; review subagents before shipping decision-changing code).

## 0. Orient (5 min)
```bash
cd ~/dev/nba && source .venv/bin/activate && set -a && source .env && set +a
git pull --rebase --autostash origin main && fantasy status
export FANTASY_RUN_ID=$(fantasy run start --agent weekly-audit --task "weekly audit $(date +%F)" --mode dry_run)
```
- If a `live` run is active or `yahoo:*` locks are held, still proceed (you don't touch Yahoo) but don't push projection
  CSV changes while the daily/tip-off run is mid-flight (check `fantasy status`; wait or skip step 5's republish).
- Read last week's audit (`docs/research/weekly_audit_*.md`, newest) and its open candidate fixes; `fantasy task list`.
- Box scores through Sunday: `fantasy data fetch-boxscores` (incremental; the daily run usually did it).

## 1. Audit (deterministic scripts)
```bash
python scripts/audit/weekly_audit.py --through <last Sunday>      # out: data/processed/audit/<date>/
```
Outputs `report.md`, `findings.json` (each finding: estimate, SE, t, suggestion; "candidate fix" if ≥ 3 SE), CSVs,
and the week-over-week comparison. It covers: standings (Yahoo-final where matchup captures exist, else reconstructed —
say which), projection bias per stat (rate vs minutes split; vs ESPN and the normal early-season pattern), heterogeneity
(tier, rookies, overrides, team changes, team-minutes), availability, minutes gainers/losers, team rotation shifts,
over/under-performers, in-season shrinkage sanity, unprojected-player coverage, matchup + category calibration
(Brier, reliability, slope, z mean/var), per-team over/under-performance vs the post-draft sim.
If the script breaks, fix it (it's ours) rather than hand-computing.

## 2. Rival moves review (what are other managers getting right?)
```bash
python scripts/audit/moves_review.py --start <last Monday> --end <last Sunday> --out data/processed/audit/<date>/moves
```
(weekly_audit.py calls it if present.) Inputs: `data/processed/league_moves_2026_27.csv` (kept current by the daily
routine's league-moves review), roster captures, box scores, news, projection history. For every rival add/drop/claim/
trade, judge it from **that manager's** perspective (not "effect on us"): ex-ante value with the projection of the day
before, ex-post production while rostered, vs the FA alternatives our tools ranked higher that day. For moves we called
bad/neutral that worked: look for the signal they plausibly used (recent minutes/role, teammate injury, games that week,
category fit/punt, unprojected rookie) and whether it would have predicted success. Cumulate across weeks (n is tiny
per week) — keep the running table in the audit note and only call a signal real once it holds across weeks (≥ ~2-3 SE
or a consistent pattern with a mechanism). Spend judgment here: this is the part scripts can't do alone.

## 3. Decide fixes
From `findings.json` + the moves review + last week's open items, list candidate fixes. A fix qualifies when:
evidence ≥ ~3 SE (or a clear bug), a mechanism, and it would change absolute numbers or decisions. Max ~2 model fixes per
week; the rest become tasks (`fantasy task add ... --area model`). Prefer fixing open tasks the audit re-confirms
(T-022..T-026 from 2026-11-09). Never change policy (autonomy, approvals, safety rules) — propose those to the user.

## 4. Implement (subagents) — model-eval §5 rules
- One subagent per fix, each with a self-contained brief (files, interfaces, OOS backtest on 2024-25/2025-26 where
  applicable, tests on synthetic data, version bump, research-note update); two fixes touching the same files run
  sequentially, not in parallel. Experiments use a scratch SQLite `FANTASY_DB_URL`, never prod.
- Adopt only if the backtest doesn't worsen error. Then `quant-reviewer` subagent on the diff; address its findings.
- Green = CI: `pytest -q`, `ruff check src tests`, `mypy src/fantasy/ops src/fantasy/db src/fantasy/data src/fantasy/league src/fantasy/cli.py src/fantasy/cli_common.py` (+ `cd web && npm run build` if web changed).

## 5. After a merged model change (user requirement)
model-eval §5: rebuild projections (`fantasy data build-projections`, `fantasy data update-projections --fetch`),
re-sim the season and this week (`fantasy sim season ...`, `fantasy sim matchup ...` as in the daily-routine skill, with
publish), confirm the dashboard shows the new runs, and record old → new odds (± SE) in the audit note and the Inbox post.

## 6. Code review + cleanup
- Scope: `git log --since="8 days ago" -- src tests scripts web` (this week's code) plus anything the audit touched.
- Run the `code-review` skill (effort high) on that range and the `simplify` skill on the files changed; also
  `ruff check --select ALL src` deltas, `vulture --min-confidence 80 src` (report-only), mypy over all of `src/fantasy`
  (count vs last week). Fix real bugs and safe cleanups (behaviour-preserving, tests green, golden outputs unchanged per
  `docs/research/tech_debt.md` protocol); larger refactors -> tasks + a line in tech_debt.md.
- Never refactor the daily-routine Yahoo write path or the ops ledger unattended beyond a clear bug fix with a test.

## 7. Report + wrap up
- `docs/research/weekly_audit_<date>.md`: headline (≤ 3 lines: biased or not, where, ± SE; are we on expectation;
  what rivals got right), standings, top findings with evidence labels, rival-moves table (cumulative), fixes made
  (versions, backtest, old → new odds), tasks added, code-review fixes. Link from the previous week's note.
- Inbox: `fantasy intel post --kind update --text "<one-line headline>" --details '{"note": "<path>"}'` — concise.
- Learn-from-mistakes: update the skill/doc/test that would have prevented any bug found; say which in the handoff.
- `fantasy run end` (succeeded/failed); handoff `docs/handoffs/<date>-weekly-audit.md`; `fantasy task done|update`;
  update STATUS.md if versions/priorities changed; commit only your paths; `git pull --rebase --autostash origin main
  && git push origin main`; `gh run list --limit 3` and fix CI if red.
