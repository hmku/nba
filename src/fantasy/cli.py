"""Top-level `fantasy` CLI. Each area registers its own Typer sub-app here."""

from __future__ import annotations

import importlib
import sys

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Fantasy basketball engine")

# (module, attribute, command name). A sub-app that fails to import is skipped (so one broken area
# cannot take down the whole CLI), but never silently: the reason goes to stderr, because otherwise
# `fantasy draft ...` would just report "No such command" and hide the real import error.
_SUBAPPS: list[tuple[str, str, str]] = [
    ("fantasy.league.cli", "app", "settings"),
    ("fantasy.data.cli", "app", "data"),
    ("fantasy.model.cli", "app", "values"),
    ("fantasy.draft.cli", "app", "draft"),
    ("fantasy.ops.cli", "run_app", "run"),
    ("fantasy.ops.cli", "lock_app", "lock"),
    ("fantasy.ops.cli", "approval_app", "approval"),
    ("fantasy.ops.cli", "txn_app", "txn"),
    ("fantasy.ops.cli", "task_app", "task"),
    ("fantasy.sim.cli", "app", "sim"),
    ("fantasy.ops.intel_cli", "app", "intel"),
    ("fantasy.inseason.cli", "app", "inseason"),
    ("fantasy.league.sync_cli", "app", "league"),
]

for _mod, _attr, _name in _SUBAPPS:
    try:
        _sub = getattr(importlib.import_module(_mod), _attr)
    except (ImportError, AttributeError) as _exc:
        print(f"warning: `fantasy {_name}` unavailable ({_mod}: {_exc!r})", file=sys.stderr)
        continue
    app.add_typer(_sub, name=_name)

try:  # top-level commands (status, publish) live in fantasy.ops.status
    from fantasy.ops.status import register as _register_status

    _register_status(app)
except ImportError:
    pass


if __name__ == "__main__":
    app()
