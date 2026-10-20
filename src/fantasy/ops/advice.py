"""Recommendations ("advice") for the dashboard's Moves view, stored as `model_runs` rows.

One recommendation = one *item* (stable `summary.advice_id`) with one or more *versions*; each version
is its own immutable `model_runs` row (`kind = 'advice'`, `model_version = ADVICE_VERSION`). A status
change (`fantasy advise status`) or an edited re-post appends a new version that copies the summary,
bumps `summary.version`, points `summary.supersedes` at the previous run_id and appends to
`summary.status_history`. Readers (the dashboard, `fantasy advise list`) show the newest version per
item. Rows are never updated in place, matching every other `model_runs` writer, and publishing
(`fantasy publish`, upsert by run_id) can therefore never clobber a newer status with an older one.

The summary schema (`advice/1`) is documented in docs/research/dashboard_data.md section 4.
advice-0.2.0 added `rationale` (plain-language why) and `why_now` (what we believed before and what
changed since). Both are optional in the schema (old rows still validate and render) but REQUIRED when
posting new or changed content for an actionable open item (`WHY_TYPES` with status in `WHY_STATUSES`);
`validate(strict=False)`, an unchanged re-post and a status change only warn.

Advice is display-only: `status = "approved"` mirrors a decision the user already made (an
`approvals` row or an explicit yes in chat); it never authorises a Yahoo write by itself.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import insert, select

from fantasy.db.schema import model_runs
from fantasy.ops.common import (
    NotFoundError,
    canonical_json,
    engine,
    git_sha,
    json_safe,
    now,
    row_dict,
    sha256_hex,
)

if TYPE_CHECKING:
    from fantasy.db.rest import RestClient

ADVICE_KIND = "advice"
ADVICE_VERSION = "advice-0.2.0"
SCHEMA = "advice/1"

TYPES = ("trade", "add_drop", "stream", "lineup", "il")
STATUSES = ("idea", "awaiting_yes", "approved", "done", "rejected", "expired")
CLOSED = frozenset({"done", "rejected", "expired"})
EVIDENCE = ("fitted", "simulated", "prior", "literature")

# Keys the writer manages; ignored when comparing content and never taken from the input file
# (except advice_id, which an author may pin).
MANAGED = frozenset({"schema", "advice_id", "version", "supersedes", "status_history"})
KNOWN = frozenset({
    "type", "title", "status", "headline", "for_date", "week", "verdict", "metrics", "scenarios",
    "scenario_columns", "risks", "requires_user_yes", "links", "approval_id", "rationale", "why_now",
}) | MANAGED
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
MAX_VERDICT_LINES = 3

# rationale / why_now (advice-0.2.0)
WHY_TYPES = frozenset({"trade", "add_drop", "stream"})
WHY_STATUSES = frozenset({"idea", "awaiting_yes"})
CHANGE_KINDS = ("news", "model", "roster", "schedule", "league", "other")
MAX_RATIONALE_ITEMS = 6
MAX_RATIONALE_CHARS = 600  # per paragraph / bullet
MAX_BEFORE_CHARS = 800
MAX_CHANGED_ITEMS = 8
MAX_CHANGED_CHARS = 400
MAX_EVIDENCE_REFS = 8
MAX_EVIDENCE_CHARS = 200
LIST_LIMIT = 2000


class AdviceError(ValueError):
    """Schema/usage problem (CLI exit code: usage)."""


# ------------------------------------------------------------------ validation


def _str(d: dict[str, Any], key: str, where: str, required: bool = True) -> str | None:
    v = d.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not v.strip():
        raise AdviceError(f"{where}{key}: expected a non-empty string, got {v!r}")
    return v.strip()


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _metric(m: Any, i: int) -> dict[str, Any]:
    where = f"metrics[{i}]."
    if not isinstance(m, dict):
        raise AdviceError(f"metrics[{i}]: expected an object")
    out: dict[str, Any] = {"label": _str(m, "label", where)}
    v = m.get("value")
    if not (_num(v) or (isinstance(v, str) and v.strip())):
        raise AdviceError(f"{where}value: expected a number or a non-empty string, got {v!r}")
    out["value"] = v
    if m.get("se") is not None:
        if not _num(m["se"]) or m["se"] < 0:
            raise AdviceError(f"{where}se: expected a non-negative number, got {m['se']!r}")
        out["se"] = m["se"]
    for k in ("unit", "note"):
        if m.get(k) is not None:
            out[k] = _str(m, k, where)
    ev = m.get("evidence")
    if ev not in EVIDENCE:
        raise AdviceError(f"{where}evidence: must be one of {list(EVIDENCE)}, got {ev!r}")
    out["evidence"] = ev
    if m.get("delta") is not None:
        if not isinstance(m["delta"], bool):
            raise AdviceError(f"{where}delta: expected true/false")
        out["delta"] = m["delta"]
    extra = set(m) - {"label", "value", "se", "unit", "note", "evidence", "delta"}
    if extra:
        raise AdviceError(f"{where[:-1]}: unknown keys {sorted(extra)}")
    return out


def _scenario(s: Any, i: int) -> dict[str, Any]:
    if not isinstance(s, dict):
        raise AdviceError(f"scenarios[{i}]: expected an object")
    _str(s, "name", f"scenarios[{i}].")
    for k, v in s.items():
        if not (v is None or isinstance(v, str | bool) or _num(v)):
            raise AdviceError(f"scenarios[{i}].{k}: cells must be scalars, got {type(v).__name__}")
    return dict(s)


def needs_why(summary: dict[str, Any]) -> bool:
    """True when `rationale` and `why_now` are required: an actionable (trade/add_drop/stream) open item."""
    return summary.get("type") in WHY_TYPES and summary.get("status") in WHY_STATUSES


def missing_why(summary: dict[str, Any]) -> list[str]:
    """The required-but-absent explanation keys of a (validated) summary; [] when none are required."""
    if not needs_why(summary):
        return []
    return [k for k in ("rationale", "why_now") if not summary.get(k)]


def validate(raw: Any, *, strict: bool = True) -> tuple[dict[str, Any], list[str]]:
    """Check an advice summary; return (normalised summary without managed keys, warnings).

    `strict` (default): an actionable open item (`needs_why`) without `rationale`/`why_now` is an
    error; otherwise only a warning (older items, unchanged re-posts)."""
    if not isinstance(raw, dict):
        raise AdviceError("advice must be a JSON object")
    warnings: list[str] = []
    unknown = set(raw) - KNOWN
    if unknown:
        warnings.append(f"unknown keys kept as-is: {sorted(unknown)}")
    s: dict[str, Any] = {k: v for k, v in raw.items() if k not in MANAGED}
    _validate_header(raw, s)
    _validate_lists(raw, s, warnings)
    _validate_why(raw, s, warnings)
    missing = missing_why(s)
    if missing:
        msg = (f"{' and '.join(missing)} required for an open {s['type']} recommendation (status "
               "idea/awaiting_yes): say in plain language why, and what changed since we decided otherwise")
        if strict:
            raise AdviceError(msg)
        warnings.append(msg)
    if not s["metrics"] and raw["type"] != "lineup":
        warnings.append("no metrics: every quantitative claim should carry an evidence label")
    if raw.get("advice_id") is not None and not ID_RE.match(str(raw["advice_id"])):
        raise AdviceError(f"advice_id: lowercase slug [a-z0-9._-], 3-100 chars, got {raw['advice_id']!r}")
    return s, warnings


def _validate_header(raw: dict[str, Any], s: dict[str, Any]) -> None:
    """type, status, the three texts, requires_user_yes, for_date, week (normalised texts go into `s`)."""
    if raw.get("type") not in TYPES:
        raise AdviceError(f"type: must be one of {list(TYPES)}, got {raw.get('type')!r}")
    if raw.get("status") not in STATUSES:
        raise AdviceError(f"status: must be one of {list(STATUSES)}, got {raw.get('status')!r}")
    for k in ("title", "headline", "verdict"):
        s[k] = _str(raw, k, "")
    if len(s["verdict"].splitlines()) > MAX_VERDICT_LINES:
        raise AdviceError(f"verdict: at most {MAX_VERDICT_LINES} lines")
    if "\n" in s["headline"]:
        raise AdviceError("headline: one line")
    if not isinstance(raw.get("requires_user_yes"), bool):
        raise AdviceError("requires_user_yes: expected true/false")
    if raw.get("for_date") is not None:
        try:
            date.fromisoformat(str(raw["for_date"]))
        except ValueError as exc:
            raise AdviceError(f"for_date: expected YYYY-MM-DD, got {raw['for_date']!r}") from exc
    if raw.get("week") is not None and (not isinstance(raw["week"], int) or isinstance(raw["week"], bool)
                                        or raw["week"] < 1):
        raise AdviceError(f"week: expected a positive integer, got {raw['week']!r}")


def _validate_lists(raw: dict[str, Any], s: dict[str, Any], warnings: list[str]) -> None:
    """metrics, scenarios (+ scenario_columns), risks, links, approval_id."""
    metrics = raw.get("metrics", [])
    if not isinstance(metrics, list):
        raise AdviceError("metrics: expected a list")
    s["metrics"] = [_metric(m, i) for i, m in enumerate(metrics)]
    if raw.get("scenarios") is not None:
        if not isinstance(raw["scenarios"], list):
            raise AdviceError("scenarios: expected a list")
        s["scenarios"] = [_scenario(x, i) for i, x in enumerate(raw["scenarios"])]
    if raw.get("scenario_columns") is not None:
        cols = raw["scenario_columns"]
        if not isinstance(cols, list) or not all(isinstance(c, str) and c for c in cols):
            raise AdviceError("scenario_columns: expected a list of column names")
        known_cols = {k for row in s.get("scenarios", []) for k in row}
        missing = [c for c in cols if c not in known_cols]
        if missing:
            warnings.append(f"scenario_columns not found in any scenario row: {missing}")
    risks = raw.get("risks", [])
    if not isinstance(risks, list) or not all(isinstance(r, str) and r.strip() for r in risks):
        raise AdviceError("risks: expected a list of strings")
    s["risks"] = risks
    links = raw.get("links", [])
    if not isinstance(links, list):
        raise AdviceError("links: expected a list")
    s["links"] = [_link(lk, i) for i, lk in enumerate(links)]
    if raw.get("approval_id") is not None:
        s["approval_id"] = _str(raw, "approval_id", "")


def _text(v: Any, where: str, limit: int) -> str:
    if not isinstance(v, str) or not v.strip():
        raise AdviceError(f"{where}: expected a non-empty string, got {v!r}")
    if len(v.strip()) > limit:
        raise AdviceError(f"{where}: at most {limit} characters (got {len(v.strip())}); keep it short")
    return v.strip()


def _validate_why(raw: dict[str, Any], s: dict[str, Any], warnings: list[str]) -> None:
    """rationale: 1-6 short strings (a bare string counts as one). why_now: {before, changed:
    [str | {text, kind?}], evidence?: [metric label]}."""
    if raw.get("rationale") is not None:
        r = raw["rationale"]
        if isinstance(r, str):
            r = [r]
        if not isinstance(r, list) or not 1 <= len(r) <= MAX_RATIONALE_ITEMS:
            raise AdviceError(f"rationale: expected 1-{MAX_RATIONALE_ITEMS} paragraph/bullet strings")
        s["rationale"] = [_text(x, f"rationale[{i}]", MAX_RATIONALE_CHARS) for i, x in enumerate(r)]
    if raw.get("why_now") is None:
        return
    w = raw["why_now"]
    if not isinstance(w, dict):
        raise AdviceError("why_now: expected an object {before, changed, evidence?}")
    extra = set(w) - {"before", "changed", "evidence"}
    if extra:
        raise AdviceError(f"why_now: unknown keys {sorted(extra)}")
    out: dict[str, Any] = {"before": _text(w.get("before"), "why_now.before", MAX_BEFORE_CHARS)}
    ch = w.get("changed")
    if not isinstance(ch, list) or not 1 <= len(ch) <= MAX_CHANGED_ITEMS:
        raise AdviceError(f"why_now.changed: expected a list of 1-{MAX_CHANGED_ITEMS} items")
    out["changed"] = [_change(c, i) for i, c in enumerate(ch)]
    if w.get("evidence") is not None:
        ev = w["evidence"]
        if not isinstance(ev, list) or len(ev) > MAX_EVIDENCE_REFS:
            raise AdviceError(f"why_now.evidence: expected a list of at most {MAX_EVIDENCE_REFS} metric labels")
        refs = [_text(x, f"why_now.evidence[{i}]", MAX_EVIDENCE_CHARS) for i, x in enumerate(ev)]
        labels = {m["label"] for m in s.get("metrics", [])}
        unknown = [x for x in refs if x not in labels]
        if unknown:
            warnings.append(f"why_now.evidence refs not matching any metric label: {unknown}")
        out["evidence"] = refs
    s["why_now"] = out


def _change(c: Any, i: int) -> str | dict[str, str]:
    where = f"why_now.changed[{i}]"
    if isinstance(c, str):
        return _text(c, where, MAX_CHANGED_CHARS)
    if not isinstance(c, dict):
        raise AdviceError(f"{where}: expected a string or {{text, kind?}}")
    extra = set(c) - {"text", "kind"}
    if extra:
        raise AdviceError(f"{where}: unknown keys {sorted(extra)}")
    out = {"text": _text(c.get("text"), f"{where}.text", MAX_CHANGED_CHARS)}
    if c.get("kind") is not None:
        if c["kind"] not in CHANGE_KINDS:
            raise AdviceError(f"{where}.kind: must be one of {list(CHANGE_KINDS)}, got {c['kind']!r}")
        out["kind"] = c["kind"]
    return out


def _link(lk: Any, i: int) -> dict[str, str | None]:
    if not isinstance(lk, dict):
        raise AdviceError(f"links[{i}]: expected an object {{label, path}}")
    return {"label": _str(lk, "label", f"links[{i}]."), "path": _str(lk, "path", f"links[{i}].")}


def content_hash(summary: dict[str, Any]) -> str:
    """Hash of the advice content, ignoring status and writer-managed keys."""
    return sha256_hex(canonical_json({k: v for k, v in summary.items() if k not in MANAGED | {"status"}}))


def slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out[:80] or "advice"


def load_file(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """An advice file is either the summary object itself, or {"summary": {...}, "params": {...},
    "inputs": {...}} when the author wants to record run parameters / input provenance."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AdviceError(f"cannot read {path}: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("summary"), dict):
        params, inputs = data.get("params") or {}, data.get("inputs") or {}
        if not isinstance(params, dict) or not isinstance(inputs, dict):
            raise AdviceError("params and inputs must be objects")
        return data["summary"], params, inputs
    return data, {}, {}


# ------------------------------------------------------------------ storage


class AdviceStore(Protocol):
    name: str

    def insert(self, row: dict[str, Any]) -> None: ...

    def rows(self, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        """`kind = 'advice'` rows, newest first."""
        ...

    def close(self) -> None: ...


class LocalStore:
    """FANTASY_DB_URL / local SQLite via SQLAlchemy."""

    name = "db"

    def insert(self, row: dict[str, Any]) -> None:
        with engine().begin() as conn:
            conn.execute(insert(model_runs).values(**row))

    def rows(self, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        with engine().connect() as conn:
            res = conn.execute(select(model_runs).where(model_runs.c.kind == ADVICE_KIND)
                               .order_by(model_runs.c.created_at.desc()).limit(limit))
            return [row_dict(r) for r in res]

    def close(self) -> None:
        pass  # the engine is shared and cached


class RestStore:
    """Supabase over HTTPS (cloud sessions); same auth as `fantasy publish --rest`."""

    name = "rest"

    def __init__(self, client: RestClient | None = None) -> None:
        from fantasy.db import rest  # local import: keeps httpx off the default path

        self._rest = rest
        self.cl = client if client is not None else rest.default_client()

    def insert(self, row: dict[str, Any]) -> None:
        payload = {k: self._rest.to_json_value(v) for k, v in row.items()}
        self.cl.insert_missing("model_runs", [payload], ["run_id"])

    def rows(self, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        resp = self.cl.request("GET", "model_runs", params={
            "select": "*", "kind": f"eq.{ADVICE_KIND}", "order": "created_at.desc", "limit": limit})
        self.cl.check(resp, "read model_runs")
        out = []
        for r in resp.json():
            if isinstance(r.get("created_at"), str):
                r["created_at"] = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            out.append(r)
        return out

    def close(self) -> None:
        self.cl.close()


def store(use_rest: bool = False, client: RestClient | None = None) -> AdviceStore:
    return RestStore(client) if use_rest else LocalStore()


# ------------------------------------------------------------------ operations


def _version_key(r: dict[str, Any]) -> tuple[int, str]:
    s = r.get("summary") or {}
    v = s.get("version") if isinstance(s, dict) else None
    ts = r.get("created_at")
    return (v if isinstance(v, int) else 0, ts.isoformat() if isinstance(ts, datetime) else str(ts or ""))


def latest_per_item(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest version per advice_id (highest summary.version, then created_at), newest item first.
    Adds `first_created_at` (when the item was first posted) and `versions` to each row."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        s = r.get("summary") or {}
        aid = s.get("advice_id") if isinstance(s, dict) else None
        by.setdefault(aid or r["run_id"], []).append(r)
    out = []
    for versions in by.values():
        versions.sort(key=_version_key)
        head = dict(versions[-1])
        head["first_created_at"] = versions[0].get("created_at")
        head["versions"] = len(versions)
        out.append(head)
    out.sort(key=lambda r: _version_key(r)[1], reverse=True)
    return out


def _versions(st: AdviceStore, advice_id: str) -> list[dict[str, Any]]:
    rows = [r for r in st.rows() if (r.get("summary") or {}).get("advice_id") == advice_id]
    rows.sort(key=_version_key)
    return rows


def _row(summary: dict[str, Any], params: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    t = now()
    return {
        "run_id": f"adv-{t:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}",
        "kind": ADVICE_KIND,
        "model_version": ADVICE_VERSION,
        "git_sha": git_sha(),
        "params": json_safe(params),
        "inputs": json_safe(inputs),
        "summary": json_safe(summary),
        "created_at": t,
    }


def _brief(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    s = row.get("summary") or {}
    return {"run_id": row["run_id"], "advice_id": s.get("advice_id"), "version": s.get("version"),
            "type": s.get("type"), "status": s.get("status"), "title": s.get("title"),
            "requires_user_yes": s.get("requires_user_yes"), "created_at": row.get("created_at"), **extra}


def post(raw: dict[str, Any], st: AdviceStore, *, params: dict[str, Any] | None = None,
         inputs: dict[str, Any] | None = None, source_file: str | None = None,
         advice_id: str | None = None) -> dict[str, Any]:
    """Validate and record one advice item. Re-posting identical content is a no-op that returns
    the latest version (its status is kept even if the file's differs: status changes go through
    `set_status`, so re-running a seed never reverts a decision); changed content appends a new
    version carrying the file's status. New or changed content of an actionable open item must carry
    `rationale` and `why_now` (`needs_why`); an unchanged re-post of an older item only warns."""
    summary, warnings = validate(raw, strict=False)
    aid = advice_id or raw.get("advice_id") or (Path(source_file).stem if source_file else None) \
        or f"{now():%Y-%m-%d}-{slug(summary['title'])}"
    aid = slug(aid) if not ID_RE.match(aid) else aid
    chash = content_hash(summary)
    prev = _versions(st, aid)
    last = prev[-1] if prev else None
    if last is not None:
        ls = last.get("summary") or {}
        if content_hash(ls) == chash:
            if ls.get("status") != summary["status"]:
                warnings.append(f"content unchanged; the latest status is {ls.get('status')!r}, not the file's "
                                f"{summary['status']!r}: use `fantasy advise status` to change it")
            return {"unchanged": True, "warnings": warnings, **_brief(last)}
    if missing_why(summary):
        validate(raw, strict=True)  # raises the explanatory AdviceError
    t = now()
    history = list((last or {}).get("summary", {}).get("status_history") or [])
    if last is None or (last.get("summary") or {}).get("status") != summary["status"]:
        history.append({"status": summary["status"], "at": t.isoformat(), "by": "post"})
    full = {"schema": SCHEMA, "advice_id": aid, "version": len(prev) + 1,
            "supersedes": last["run_id"] if last else None, **summary, "status_history": history}
    row = _row(full, {"command": "post", **(params or {})},
               {"source_file": source_file, "content_sha256": chash, **(inputs or {})})
    st.insert(row)
    return {"unchanged": False, "warnings": warnings, **_brief(row)}


def resolve(st: AdviceStore, ref: str) -> list[dict[str, Any]]:
    """All versions of the item named by an advice_id or any of its run_ids (oldest first)."""
    rows = st.rows()
    aid = next((((r.get("summary") or {}).get("advice_id")) for r in rows if r["run_id"] == ref), None) or ref
    versions = [r for r in rows if (r.get("summary") or {}).get("advice_id") == aid]
    if not versions:
        raise NotFoundError(f"no advice with advice_id or run_id {ref!r}", ref=ref)
    versions.sort(key=_version_key)
    return versions


def set_status(st: AdviceStore, ref: str, status: str, *, note: str | None = None,
               approval_id: str | None = None) -> dict[str, Any]:
    """Append a version of the item with a new status (no-op if unchanged)."""
    if status not in STATUSES:
        raise AdviceError(f"status: must be one of {list(STATUSES)}, got {status!r}")
    if status == "approved" and not (approval_id or note):
        raise AdviceError("status approved needs --approval-id (approvals table) or --note saying where "
                          "the user said yes; it never authorises a Yahoo write by itself")
    versions = resolve(st, ref)
    last = versions[-1]
    ls = dict(last.get("summary") or {})
    if ls.get("status") == status and approval_id in (None, ls.get("approval_id")):
        return {"unchanged": True, **_brief(last)}
    t = now()
    entry: dict[str, Any] = {"status": status, "at": t.isoformat(), "by": "status", "from": ls.get("status")}
    if note:
        entry["note"] = note
    if approval_id:
        entry["approval_id"] = approval_id
        ls["approval_id"] = approval_id
    ls.update({"status": status, "version": len(versions) + 1, "supersedes": last["run_id"],
               "status_history": [*(ls.get("status_history") or []), entry]})
    row = _row(ls, {"command": "status", "from_status": entry["from"], "to_status": status, "note": note,
                    "approval_id": approval_id},
               {"previous_run_id": last["run_id"], "content_sha256": content_hash(ls)})
    st.insert(row)
    missing = missing_why(ls)
    warnings = [f"{' and '.join(missing)} missing: this open recommendation has no plain-language "
                "justification; re-post it with them"] if missing else []
    return {"unchanged": False, "previous_run_id": last["run_id"], "warnings": warnings, **_brief(row)}


def list_advice(st: AdviceStore, *, status: str | None = None, all_versions: bool = False,
                open_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    rows = st.rows()
    items = rows if all_versions else latest_per_item(rows)
    out = []
    for r in items:
        s = r.get("summary") or {}
        if status and s.get("status") != status:
            continue
        if open_only and s.get("status") in CLOSED:
            continue
        out.append(_brief(r, headline=s.get("headline"), first_created_at=r.get("first_created_at"),
                          versions=r.get("versions")))
    return out[:limit]
