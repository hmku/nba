"""Shared CLI output helpers for every `fantasy` command group: JSON emission, failures with
machine-readable codes, and argument checks.

Failure JSON contract (all groups, with ``--json``)::

    {"ok": false, "code": "<machine code>", "message": "<human text>", "error": ..., ...extra}

- ``code`` is the field to branch on. Values: ``usage`` (bad arguments), ``not_found``, ``conflict``,
  ``lock_held``, ``not_allowed``, ``duplicate``, ``invalid_state``, ``still_pending``, ``rest_auth``,
  ``unresolved_player``, ``draft_complete``, ``order_conflicts_with_recorded_picks`` (`draft set-order`, exit 2),
  ``error`` (anything else).
- ``error`` keeps its historical meaning per group so existing callers do not break: in ops commands
  (`run`, `lock`, `approval`, `txn`, `task`, `status`, `publish`) and the ops-backed draft commands
  (`prepare-pick` refusals, `confirm-pick` ledger errors) it equals ``code``; in `draft`, `sim`,
  `values`, `settings`, `league`, `lineup` it is the human message (== ``message``).
  Group-specific codes: `lineup plan` ``stale_capture`` / ``infeasible_roster`` (exit 2);
  `league sync|import-capture` ``invalid_capture``.
- Exit codes are unchanged per command (``fantasy.ops.common.EXIT_*``; see docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, NoReturn

import typer

from fantasy.ops.common import (
    EXIT_CONFLICT,
    EXIT_DUPLICATE,
    EXIT_ERROR,
    EXIT_INVALID_STATE,
    EXIT_NOT_ALLOWED,
    EXIT_NOT_FOUND,
    EXIT_STILL_PENDING,
    EXIT_USAGE,
    json_safe,
)

# default machine code for an exit code when the caller does not name one
CODE_FOR_EXIT: dict[int, str] = {
    EXIT_ERROR: "error",
    EXIT_USAGE: "usage",
    EXIT_CONFLICT: "conflict",
    EXIT_NOT_ALLOWED: "not_allowed",
    EXIT_DUPLICATE: "duplicate",
    EXIT_NOT_FOUND: "not_found",
    EXIT_INVALID_STATE: "invalid_state",
    EXIT_STILL_PENDING: "still_pending",
}


def dumps(obj: Any, *, indent: int | None = 2, sort_keys: bool = False) -> str:
    return json.dumps(json_safe(obj), indent=indent, sort_keys=sort_keys)


def emit(obj: dict, as_json: bool, text: str | None = None, *, indent: int | None = 2,
         sort_keys: bool = False) -> None:
    """Print `obj` as JSON (with --json, or when there is no text form), else `text`."""
    if as_json or text is None:
        typer.echo(dumps(obj, indent=indent, sort_keys=sort_keys))
    else:
        typer.echo(text)


def failure(message: str, code: str, *, error: str | None = None, **extra: Any) -> dict[str, Any]:
    """The failure payload (see module docstring); `error` defaults to the message."""
    return json_safe({"ok": False, "error": message if error is None else error, "code": code,
                      "message": message, **extra})


def fail(message: str, as_json: bool, exit_code: int = EXIT_ERROR, code: str | None = None,
         **extra: Any) -> NoReturn:
    """Print a failure (JSON on stdout with --json, `error: ...` on stderr otherwise) and exit."""
    if as_json:
        typer.echo(json.dumps(failure(message, code or CODE_FOR_EXIT.get(exit_code, "error"), **extra)))
    else:
        typer.echo(f"error: {message}", err=True)
    raise typer.Exit(exit_code)


def check_choice(value: str | None, valid: Sequence[str], what: str, as_json: bool) -> None:
    """Exit 2 (code `usage`) listing the valid values if `value` is not one of them (None = not given)."""
    if value is not None and value not in valid:
        fail(f"invalid {what} {value!r}; valid: {', '.join(valid)}", as_json, EXIT_USAGE, valid=list(valid))


def parse_punt(punt: str | None, categories: list[str], as_json: bool) -> list[str]:
    """Comma-separated category keys; exit 2 (code `usage`) listing the valid keys on anything unknown."""
    cats = [p.strip() for p in (punt or "").split(",") if p.strip()]
    bad = [c for c in cats if c not in categories]
    if bad:
        fail(f"invalid punt categor{'y' if len(bad) == 1 else 'ies'} {', '.join(bad)}; "
             f"valid: {', '.join(categories)}", as_json, EXIT_USAGE, valid=list(categories))
    return cats
