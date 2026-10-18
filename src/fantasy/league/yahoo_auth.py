"""One-time interactive Yahoo OAuth2 authorization (run LOCALLY, not in a cloud session).

Usage:  ``fantasy settings yahoo-auth``  (or ``python -m fantasy.league.yahoo_auth``)

1. Create an app at https://developer.yahoo.com/apps/create/ : type "Installed Application",
   Redirect URI ``https://localhost:8080`` (Yahoo requires an https URI to register), API
   permissions "Fantasy Sports" -> Read (Read/Write only if write ops are ever enabled).
2. Run this command; paste Client ID / Secret (or export YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET).
3. Approve in the browser. With the default redirect ``https://localhost:8080`` the browser lands
   on a dead page whose URL contains ``?code=...``; paste the full URL (or just the code). Legacy
   apps registered with ``oob`` can pass ``--redirect-uri oob`` (Yahoo then shows the code on the page;
   ``oob`` no longer works for new apps).

Tokens are written to ``~/.config/fantasy/yahoo_oauth.json`` (mode 0600) and never printed.
"""

from __future__ import annotations

import getpass
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from fantasy.league.yahoo_api import (
    AUTH_URL,
    DEFAULT_REDIRECT_URI,
    TOKEN_URL,
    YahooAuthError,
    YahooCredentials,
    apply_token_response,
    save_credentials,
    token_path,
)


def build_authorize_url(client_id: str, redirect_uri: str = DEFAULT_REDIRECT_URI) -> str:
    q = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "language": "en-us",
    }
    return f"{AUTH_URL}?{urlencode(q)}"


def extract_code(user_input: str) -> str:
    """Accept a bare code or a redirect URL containing ``code=``."""
    s = user_input.strip()
    if "code=" in s:
        qs = parse_qs(urlparse(s).query) or parse_qs(s.split("?", 1)[-1])
        codes = qs.get("code")
        if codes:
            return codes[0]
    if not s:
        raise YahooAuthError("no authorization code provided")
    return s


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    http: httpx.Client | None = None,
) -> YahooCredentials:
    client = http or httpx.Client(timeout=20.0)
    try:
        resp = client.post(
            TOKEN_URL,
            auth=(client_id, client_secret),
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    finally:
        if http is None:
            client.close()
    if resp.status_code != 200:
        raise YahooAuthError(
            f"code exchange failed (HTTP {resp.status_code}). Codes are single-use and short-lived; "
            "check the redirect URI matches the one used to authorize."
        )
    tok = resp.json()
    if not tok.get("refresh_token"):
        raise YahooAuthError("token response had no refresh_token")
    creds = YahooCredentials(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=tok["refresh_token"],
        redirect_uri=redirect_uri,
    )
    apply_token_response(creds, tok)
    return creds


def run_interactive_auth(
    client_id: str | None = None,
    client_secret: str | None = None,
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    path: Path | None = None,
    open_browser: bool = True,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    http: httpx.Client | None = None,
    out: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> Path:
    """Interactive flow; returns the path of the saved token file."""
    import os

    client_id = (
        client_id or os.environ.get("YAHOO_CLIENT_ID") or input_fn("Yahoo Client ID: ").strip()
    )
    client_secret = (
        client_secret
        or os.environ.get("YAHOO_CLIENT_SECRET")
        or secret_fn("Yahoo Client Secret (hidden): ").strip()
    )
    if not client_id or not client_secret:
        raise YahooAuthError("client id and secret are required")
    url = build_authorize_url(client_id, redirect_uri)
    out(f"Open this URL, sign in to Yahoo and approve access:\n  {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - headless machines
            pass
    code = extract_code(input_fn("Paste the code (or the full redirect URL): "))
    creds = exchange_code(client_id, client_secret, code, redirect_uri, http=http)
    saved = save_credentials(creds, path or token_path())
    out(f"Saved Yahoo OAuth tokens to {saved} (mode 0600). Do not commit this file.")
    return saved


def cli_command(
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    no_browser: bool = False,
) -> None:
    """Typer-compatible entry point: ``app.command("yahoo-auth")(cli_command)``."""
    try:
        run_interactive_auth(redirect_uri=redirect_uri, open_browser=not no_browser)
    except YahooAuthError as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Authorize this machine against the Yahoo Fantasy API")
    ap.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cli_command(redirect_uri=a.redirect_uri, no_browser=a.no_browser)
