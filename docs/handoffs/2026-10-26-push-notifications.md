# Handoff 2026-10-26 - phone notifications for approvals (main session + subagent, local)

## What was accomplished
- Web Push for new pending approvals: Supabase DB webhook on `approvals` INSERT → `POST /api/push/notify` (Bearer
  `PUSH_NOTIFY_SECRET`) → `web-push` to stored subscriptions; idempotent via `push_sent`; tap opens `/inbox#<approval_id>`.
- Inbox "Enable notifications" (passphrase-gated), Test, Turn off; iPhone needs Home Screen install.
- Tables `push_subscriptions`, `push_sent` (RLS on, no anon privileges; skipped by `fantasy publish`).
- Runbook `docs/runbooks/dashboard.md` "Notifications"; production steps in `docs/USER_TODO.md`.

## Decisions & rationale
- Trigger = Supabase webhook: fires for every way an approval is created (CLI, txn plan, --rest, publish). Vercel Cron
  rejected (Hobby: daily only); CLI-side call rejected (misses other creation paths, spreads the secret).

## Tests performed
- pytest, ruff, CI mypy green; `npm run lint`, `npm test` (157), `npm run build` green (subagent run).
- Not yet tested end to end on a phone: needs the production steps (migration, Vercel env vars, webhook).

## Outstanding
- User: USER_TODO "Phone notifications" steps 2-6. Then an agent files a harmless test approval to verify.
- `npm audit`: next/postcss advisories (pre-existing; fix = next 15.5.28) — separate bump.
