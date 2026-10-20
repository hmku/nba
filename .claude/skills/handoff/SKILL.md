---
name: handoff
description: Session start and end protocol for this repo - orient from docs/STATUS.md, handoff notes and `fantasy status`; at the end write docs/handoffs/YYYY-MM-DD-<topic>.md from the template, update STATUS.md and the task backlog, run tests, and commit+push only your own paths. Use at the start of every session, whenever you finish a unit of work, before context runs out, or when the user says "wrap up" / "hand off".
---

# Session handoff protocol

Purpose: the next session (possibly a different agent, cloud or local) must be effective immediately.
Markdown holds knowledge (docs, handoffs); the DB holds state (runs, tasks, txns). Keep both current.

## Session start
1. The SessionStart hook prints `fantasy status`, the latest handoffs and a pointer to STATUS.md.
   If it did not run: `pip install -e ".[dev]" -q` (if `fantasy` is missing), then `fantasy status`.
2. Read `docs/STATUS.md`, then the newest 1-3 files in `docs/handoffs/` (filenames sort by date).
3. `fantasy task list` -> pick work matching the user's request; claim it:
   `fantasy task claim T-00N --owner <your-agent-name>` (exit 3 = someone else owns it: pick another
   or ask). `fantasy task show T-00N --json` for the description.
4. Anything touching Yahoo or the ops ledger: follow the `agent-run` skill.
5. Know where you are: cloud sessions (`CLAUDE_CODE_REMOTE=true`) reach the allowlisted public sources
   (basketball-reference, Yahoo public pages/API, ESPN, CBS, ...; stats.nba.com times out) and Supabase only via
   `--rest`; Yahoo *account* work needs a local session with browser tools (`yahoo-browser` skill).

## Session end (mandatory, also when stopping early)
1. **Tests**: `pytest` and `ruff check src tests` (plus `cd web && npm run build` if `web/` changed).
   Record results honestly in the note (what ran, pass/fail counts, failures that are not yours).
2. **Ops state**: release locks you hold, `fantasy run end --summary ...` for open runs (agent-run skill).
3. **Tasks**: `fantasy task done T-00N` (exit 3 if another owner holds it), or
   `fantasy task update T-00N --status blocked|todo --description "..." [--release]` for unfinished work.
   New work discovered: `fantasy task add "title" --area <data|model|draft|web|infra|agent|season> --priority 0-3 --description "..."`
   (exit 5 = title exists). Task state lives in the DB you are connected to - say which DB in the note.
4. **Handoff note**: `docs/handoffs/YYYY-MM-DD-<topic>.md` (date = today, UTC; topic = short kebab
   slug; if the file exists add `-2`). Use the template below.
5. **STATUS.md**: edit `docs/STATUS.md` when the state table, next priorities or known limitations
   changed (keep it short; it's the first thing every session reads). Update the "_Last updated_" line.
6. **Commit and push** (user requirement, every session):
   ```bash
   git status --short                      # others edit src/, web/, data/ concurrently
   git add <your paths only>
   git commit -m "<Area>: <what>" -- <your paths only>   # never `git add -A` / `git commit -a`
   git pull --rebase --autostash origin main && git push origin main
   ```
   - Commit message ends with the attribution lines your session's instructions specify (if any).
   - `index.lock` exists -> another process is committing: wait a few seconds and retry; don't delete it
     unless no git process is running.
   - Rebase conflict in a file you don't own: `git rebase --abort`, tell the user; never resolve
     someone else's file by discarding their changes.
   - Never commit secrets (`.env`, DB URLs, Yahoo tokens in `~/.config/fantasy/`), `var/*.db`, or
     screenshots containing account details.

## Handoff note template
```markdown
# Handoff YYYY-MM-DD - <topic> (<agent name>, <cloud|local> session)

## What was accomplished
- <bullets: features, data, decisions executed; link files/commands>

## Decisions & rationale
- <decision> - <why; alternatives rejected; who approved if the user did>

## Files changed
- `path` - <one line>

## Tests performed
- `pytest` -> N passed / M failed (<which, whose>); `ruff check src tests` -> clean; manual checks: <commands + key output>

## Problems encountered
- <bugs, blocked network, flaky tools, data conflicts; how resolved or not>

## Outstanding work
- <unfinished items with task ids (T-00N) and current state>

## Suggested next steps
1. <ordered, concrete; say whether it needs a local/browser session>

## Notes for next agent
- <gotchas, env vars needed, DB used (Supabase vs SQLite), runs/locks/txns left open and why>
```
Keep it factual and short (one screen if possible). Numbers over adjectives. If you changed a model,
include old vs new `MODEL_VERSION` and the effect on top players.
