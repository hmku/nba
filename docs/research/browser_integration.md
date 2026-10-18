# Browser integration for Yahoo (draft room, lineups, verification)

Why a browser at all: the Yahoo API cannot make draft picks (see `yahoo_api.md` §5), the live draft is
60 s/pick, and league pages (settings, draft order) are login-gated. The agent therefore drives the
**user's own, already-logged-in browser**, with the user watching.

## Recommended: Claude in Chrome + a LOCAL Claude Code session

Source: [code.claude.com/docs/en/chrome](https://code.claude.com/docs/en/chrome) (fetched 2026-10-18).

* **What it is**: the "Claude in Chrome" extension (Chrome Web Store id
  `fcoeoabgfenejglbffodgkkbkcdhcgfn`, **v1.0.36+**) connects to Claude Code via a native-messaging
  host. Claude opens its own tabs (grouped in a tab group tied to the session) in your real browser,
  **sharing its cookies/logins**, in a visible window. Login pages and CAPTCHAs: Claude pauses and asks
  you to handle them.
* **Requirements**: Google Chrome or Microsoft Edge (also detected: Brave, Arc, Vivaldi, Opera);
  **not supported in WSL**; Claude Code signed in with `/login` on a **direct Anthropic plan (Pro,
  Max, Team, Enterprise)** — API-key / `setup-token` auth disables it; not available via
  Bedrock/Vertex/Foundry. Extension and Claude Code must be signed in to the same claude.ai org.
* **Platforms**: macOS, Linux, Windows (native). Native host manifest:
  macOS `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.anthropic.claude_code_browser_extension.json`,
  Linux `~/.config/google-chrome/NativeMessagingHosts/…`, Windows registry `HKCU\Software\Google\Chrome\NativeMessagingHosts\`.
* **Enable**: `claude --chrome`, or inside a session run `/chrome` (status, permissions, reconnect,
  select browser, **Enabled by default**). Working when `/chrome` shows "Status: Enabled" and
  "Extension: Installed". A project's `.claude/settings.json` **cannot** turn it on
  (`CLAUDE_CODE_ENABLE_CFC` is ignored there) — the user must opt in.
* **Tools**: exposed as an MCP server `claude-in-chrome`, tools named `mcp__claude-in-chrome__*`
  (navigate, read page/DOM, find, click, form input, screenshot, console logs, tabs, GIF recording,
  `tabs_context_mcp`, `browser_batch`, …). List them with `/mcp` → `claude-in-chrome` → View tools.
  The project's `yahoo-browser` / `live-draft` skills should load them with one ToolSearch call.
* **Permissions**: per-site permissions live in the extension; the first action on a site prompts
  "Claude in Chrome wants to …" with "allow all actions on this site for the session". **Pre-approve
  `basketball.fantasysports.yahoo.com` before the draft** so no prompt blocks a 60 s clock.
* **Limitations / gotchas**: JS alert/confirm dialogs block the extension (dismiss manually); the
  service worker can idle in long sessions → `/chrome` → "Reconnect extension" ("Receiving end does not
  exist"); "No tab available" → ask for a new tab; enabling by default adds tool context to every
  session; screenshots/GIFs capture account details.

### Cloud sessions (like the one that wrote this)
A Claude Code **cloud** session runs in an Anthropic VM: it has **no access to the user's Chrome**,
and in this project's environment Yahoo hosts are blocked by the egress proxy anyway. So:
* Research, modelling, data, DB, dashboard: cloud is fine.
* **Anything touching Yahoo (auth, settings verification, live draft, lineups, waivers) must run in a
  LOCAL Claude Code session** on the user's computer with `--chrome`.
* To watch/steer the local session from a phone during the draft, use **Remote Control**
  (`/remote-control` in the local session; continues on claude.ai/code or the Claude app while
  execution, MCP servers and the browser stay local). `claude --teleport <session>` pulls a cloud
  session's conversation + branch into the local terminal if needed.
* (Some Claude desktop-app setups let a cloud session reach a linked computer's built-in browser /
  computer-use tools via `mcp__remote-devices__*`; that is a different browser without the user's
  Chrome logins and is not part of this plan.)

## Alternative: Playwright MCP
`claude mcp add playwright npx @playwright/mcp@latest` ([microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp)).
* Default is a **persistent profile** per workspace (`--user-data-dir` to choose it): log in to Yahoo
  once inside the Playwright browser and the session persists. `--isolated` + `--storage-state` for
  ephemeral sessions. `--extension` (Playwright extension) or `--cdp-endpoint` attaches to an existing
  Chrome.
* Pros: works with any auth method (API key, Bedrock), deterministic accessibility-tree snapshots,
  headless option, no dependence on the extension's service worker.
* Cons: a separate browser profile (Yahoo may challenge a fresh/automation-flagged browser with
  CAPTCHA/2FA; must log in again), extra Node dependency, automation fingerprint risk on Yahoo, and the
  user is not "already there" watching their usual window.

**Recommendation: Claude in Chrome** (matches ARCHITECTURE.md "No custom browser bot"; user's real
logged-in Chrome, visible, human-in-the-loop, zero extra infra). Keep Playwright MCP with a persistent
profile as the **fallback** if the extension won't connect on draft night. Either way the Python
engine never touches Yahoo's UI; the agent reads the board, calls `fantasy draft pick/recommend`, and
clicks.

## User setup steps (do today, 2026-10-18 — not at 9:55pm tomorrow)
1. Install/update Claude Code locally (`claude --version`; update to the latest release) and sign in
   with `/login` (claude.ai account on Pro/Max/Team/Enterprise — not an API key).
2. In Chrome, install **Claude** from the Chrome Web Store
   (https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn), sign it in to the
   same claude.ai account/org as Claude Code (`/status` → Organization).
3. In Chrome, log in to Yahoo and open https://basketball.fantasysports.yahoo.com/nba/44495 once.
4. `git clone https://github.com/hmku/nba && cd nba && pip install -e '.[dev]'`.
5. Start `claude --chrome` in the repo. First time: accept the intro dialog; if the extension isn't
   detected, **restart Chrome** (it reads the native host file on startup), then `/chrome` →
   "Reconnect extension". Confirm "Status: Enabled / Extension: Installed". Optionally `/chrome` →
   **Enabled by default**.
6. Ask Claude to open the league settings page; when prompted, choose **allow all actions on this site**
   for `basketball.fantasysports.yahoo.com`. Have it record settings + the draft order page (verifies
   3RR, see `draft_order.md`) into `fantasy settings`.
7. (Optional, for API cross-checks) create a Yahoo app (Installed Application, redirect
   `https://localhost:8080`, Fantasy Sports Read) and run `fantasy settings yahoo-auth` locally.
8. Do a **Yahoo mock draft** with Claude driving (Yahoo "Mock Draft Lobby") to rehearse picks under a
   60 s clock; set Yahoo **Pre-Draft Rankings** from the engine board as the autopick safety net.
9. Draft night: keep the laptop awake and plugged in, Chrome in the foreground window, no other tab
   group interference; run `/remote-control` if you want to monitor from your phone.
