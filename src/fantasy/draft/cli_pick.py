"""`fantasy draft prepare-pick|confirm-pick` — the transactional pick flow around clicking Draft on Yahoo
(txn ledger, run mode, locks). Registered on `fantasy.draft.cli.app`."""

from __future__ import annotations

import json
from typing import Annotated, NoReturn

import typer

from fantasy.cli_common import emit, fail, failure
from fantasy.draft import store
from fantasy.draft.cli import DraftOpt, JsonOpt, ProjOpt, _load_draft, app, behind_clock
from fantasy.draft.names import NameResolutionError, resolve_player
from fantasy.draft.order import order_for_draft
from fantasy.model.lines import load_projections

RunOpt = Annotated[str | None, typer.Option("--run-id", envvar="FANTASY_RUN_ID", help="Agent run id")]


@app.command("prepare-pick")
def prepare_pick(
    player: Annotated[str, typer.Argument(help="Player to draft (name or player_id)")],
    overall: Annotated[int, typer.Option("--overall", help="My overall pick number")],
    rationale: Annotated[str, typer.Option("--rationale")] = "",
    draft_id: DraftOpt = None,
    run_id: RunOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """One call before clicking Draft: check the board, log decision vs latest recommendation, plan the txn,
    mark it submitted.

    Exit 0 with action=CLICK → click Draft in Yahoo now. Exit 4 → NOT allowed (dry_run, no lock, not my pick,
    pick already on the board, player already drafted, board not synced = an earlier pick is not recorded,
    listed in `behind`): do not click. Exit 7 → this pick already has a
    submitted/verified txn: verify on Yahoo instead of clicking again. A failed/cancelled earlier attempt is
    retried automatically (next attempt number); a `planned` txn left by another run is re-owned.
    """
    from fantasy.ops import ledger, transactions
    from fantasy.ops.common import OpsError

    if not run_id:
        fail("no run id (set FANTASY_RUN_ID)", as_json, exit_code=2)
    d = _load_draft(draft_id, as_json)
    did = d["draft_id"]
    values = load_projections(projections).df
    try:
        pid = resolve_player(player, values)
    except NameResolutionError as e:
        fail(str(e), as_json, exit_code=2, code="unresolved_player", candidates=e.candidates, draft_id=did)
    name = str(values.loc[pid, "name"])

    picks = store.get_picks(did)
    taken = {p["overall_pick"]: p for p in picks}
    refusal = _board_refusal(d, overall, pid, name, picks)
    if refusal:
        _refuse(refusal[0], as_json, did, **refusal[1])
    top, rank_in_recs, gaps, warnings = _vs_latest_recommendation(did, pid, overall, taken)
    behind = behind_clock(taken, overall)
    if behind:  # the clock is at my pick only if every earlier pick is on the board (gaps alone miss 21..23 of 1..20)
        mine_behind = _behind_mine(d, behind)
        hint = "".join(f" (your #{m['overall']}: confirm-pick {m['txn_id']})" for m in mine_behind if m["txn_id"])
        _refuse(f"board not synced: picks {behind} not recorded — record them from the draft log first{hint}",
                as_json, did, behind=behind, behind_mine=mine_behind, gaps=gaps)
    mode = None
    try:
        mode = ledger.get_run(run_id)["mode"]
        dec, txn, txn_note = _decide_and_submit(run_id, did, pid, name, overall, mode, top, rationale)
    except OpsError as e:
        if as_json:
            typer.echo(json.dumps({**e.to_dict(), "action": "DO_NOT_CLICK", "draft_id": did, "run_mode": mode,
                                   "gaps": gaps, "behind": behind, "warnings": warnings}, default=str))
        else:
            typer.echo(f"DO NOT CLICK: {e.message}", err=True)
        raise typer.Exit(e.exit_code) from e
    out = {"ok": True, "action": "CLICK", "draft_id": did, "player_id": pid, "name": name,
           "team": _str(values.loc[pid, "team"]), "overall": overall, "txn_id": txn["txn_id"],
           "attempt": transactions.attempt_of(txn), "txn_note": txn_note, "run_mode": mode,
           "agrees_with_model": dec.get("agrees_with_model"),
           "rank_in_recommendations": None if rank_in_recs is None else rank_in_recs + 1,
           "gaps": gaps, "behind": behind, "warnings": warnings}
    emit(out, as_json, f"CLICK DRAFT: {name} ({out['team']}) at #{overall}  txn={txn['txn_id']}  "
                        f"agrees_with_model={out['agrees_with_model']} {warnings or ''}")


def _refuse(msg: str, as_json: bool, draft_id: str, **extra: object) -> NoReturn:
    """prepare-pick policy refusal: exit 4, ops-style JSON (error == code == not_allowed), DO_NOT_CLICK."""
    from fantasy.ops.common import EXIT_NOT_ALLOWED

    if as_json:
        typer.echo(json.dumps(failure(msg, "not_allowed", error="not_allowed", action="DO_NOT_CLICK",
                                      draft_id=draft_id, **extra)))
    else:
        typer.echo(f"DO NOT CLICK: {msg}", err=True)
    raise typer.Exit(EXIT_NOT_ALLOWED)


def _board_refusal(d: dict, overall: int, pid: str, name: str, picks: list[dict]) -> tuple[str, dict] | None:
    """Why the board forbids drafting `pid` at `overall` (message, extra JSON), or None if it is fine."""
    taken = {p["overall_pick"]: p for p in picks}
    mine = [p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]]
    if overall not in mine:
        return f"pick {overall} is not my pick (slot {d['my_slot']}); my picks: {mine}", {"my_picks": mine}
    if overall in taken:
        return (f"pick {overall} is already recorded on the board: {taken[overall]['player_name']}",
                {"pick": taken[overall]})
    already = next((p for p in picks if p["player_id"] == pid), None)
    if already:
        return f"{name} is already recorded as drafted at pick {already['overall_pick']}", {"pick": already}
    return None


def _behind_mine(d: dict, behind: list[int]) -> list[dict]:
    """My own picks among the unrecorded `behind` picks, each with its submitted txn (if any): such a pick was
    clicked on Yahoo but never confirmed, so the fix is `confirm-pick <txn_id>`, not `draft pick`."""
    from fantasy.ops import transactions

    mine = {p.overall for p in order_for_draft(d) if p.slot == d["my_slot"]}
    out = []
    for k in behind:
        if k not in mine:
            continue
        sub = [t for t in transactions.draft_pick_txns(d["draft_id"], k) if t["status"] == "submitted"]
        out.append({"overall": k, "txn_id": sub[-1]["txn_id"] if sub else None})
    return out


def _vs_latest_recommendation(
    did: str, pid: str, overall: int, taken: dict[int, dict],
) -> tuple[dict | None, int | None, list[int], list[str]]:
    """(model's top choice, 0-based rank of `pid` in the latest recommendation, board gaps, warnings)."""
    rec = store.latest_recommendation(did) or {}
    recs = rec.get("recommendations") or []
    top = {k: recs[0].get(k) for k in ("player_id", "name", "score")} if recs else None
    rank_in_recs = next((i for i, r in enumerate(recs) if r.get("player_id") == pid), None)
    warnings = []
    gaps = [k for k in range(1, max(taken, default=0)) if k not in taken]
    if gaps:
        warnings.append(f"Picks {gaps} not recorded — availability may be wrong; sync the board first.")
    if rec.get("for_pick") not in (None, overall):
        warnings.append(f"latest recommendation was for pick {rec.get('for_pick')}, not {overall}")
    if rank_in_recs is None:
        warnings.append("chosen player is not in the latest recommendation list")
    return top, rank_in_recs, gaps, warnings


def _decide_and_submit(run_id: str, did: str, pid: str, name: str, overall: int, mode: str,
                       top: dict | None, rationale: str) -> tuple[dict, dict, str]:
    """Log the decision, plan (or reuse/retry) the pick txn and mark it submitted. Raises OpsError
    (policy: dry_run/lock -> not_allowed; already submitted/verified -> invalid_state)."""
    from fantasy.ops import ledger, transactions

    choice = {"player_id": pid, "name": name, "overall": overall, "mode": mode}
    dec = ledger.record_decision(run_id, "draft_pick", top, choice, rationale or None)
    payload = {"draft_id": did, "overall_pick": overall, "player_id": pid}
    txn, txn_note = _txn_for_pick(did, overall, payload, run_id, f"Draft {name} at #{overall}")
    txn = transactions.mark_submitted(txn["txn_id"], run_id)
    return dec, txn, txn_note


def _txn_for_pick(draft_id: str, overall: int, payload: dict, run_id: str, summary: str) -> tuple[dict, str]:
    """The txn to submit for (draft_id, overall).

    submitted/verified exists → InvalidStateError (exit 7: never click twice). A planned txn for the same
    player is reused (re-owned if another run, e.g. a dry run, left it); planned txns for other players are
    cancelled. Otherwise plan a new txn with the next attempt number (automatic retry after failed/cancelled).
    """
    from fantasy.ops import transactions
    from fantasy.ops.common import InvalidStateError

    existing = transactions.draft_pick_txns(draft_id, overall)
    done = [t for t in existing if t["status"] in ("submitted", "verified")]
    if done:
        t = done[-1]
        raise InvalidStateError(
            f"pick {overall} already has a {t['status']} transaction {t['txn_id']}: verify on Yahoo and "
            "reconcile with `fantasy draft confirm-pick`, never click again", existing=t)
    reuse, note = None, ""
    for t in existing:
        if t["status"] not in ("planned", "awaiting_approval"):
            continue
        if reuse is None and t["payload"] == payload:
            if t["run_id"] == run_id:
                reuse, note = t, "reused"
            else:
                reuse, note = transactions.reassign(t["txn_id"], run_id), f"re-owned from {t['run_id']}"
        else:
            transactions.cancel(t["txn_id"], f"superseded by prepare-pick in run {run_id}")
    if reuse is not None:
        return reuse, note
    attempt = max((transactions.attempt_of(t) for t in existing), default=0) + 1
    txn = transactions.plan("draft_pick", payload, run_id=run_id, attempt=attempt, summary=summary)
    return txn, ("new" if attempt == 1 else f"retry (attempt {attempt})")


@app.command("confirm-pick")
def confirm_pick(
    txn_id: Annotated[str, typer.Argument()],
    evidence: Annotated[str, typer.Option("--evidence", help="What on Yahoo shows the pick (text or JSON)")] = "",
    failed: Annotated[str | None, typer.Option("--failed", help="Mark failed with this reason instead")] = None,
    run_id: RunOpt = None,
    projections: ProjOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """After clicking: verify (records the pick on the board) or mark failed. Never click twice.

    Idempotent: on a txn that is already `verified` (e.g. via `txn mark verified`) it just records the
    pick on the board and exits 0."""
    from fantasy.ops import transactions
    from fantasy.ops.common import OpsError

    already_verified = False
    try:
        txn = transactions.get_txn(txn_id)
        if txn["kind"] != "draft_pick":
            fail(f"{txn_id} is a {txn['kind']} transaction, not draft_pick; use `fantasy txn mark`", as_json, exit_code=2)
        did = (txn["payload"] or {}).get("draft_id")
        if failed:
            txn = transactions.mark_failed(txn_id, failed, run_id=run_id)
            emit({"ok": True, "draft_id": did, "txn": txn}, as_json, f"marked failed: {failed}")
            return
        if txn["status"] == "verified":
            already_verified = True
        else:
            if not evidence:
                fail("--evidence is required (e.g. 'draft log shows pick 24 = X for my team')", as_json, exit_code=2)
            try:
                ev = json.loads(evidence)
            except json.JSONDecodeError:
                ev = {"note": evidence}
            txn = transactions.mark_verified(txn_id, ev, run_id=run_id)
    except OpsError as e:
        typer.echo(json.dumps(e.to_dict(), default=str) if as_json else f"error: {e.message}")
        raise typer.Exit(e.exit_code) from e
    p = txn["payload"]
    d = store.get_draft(p["draft_id"])
    if d is None:
        fail(f"draft {p['draft_id']} not found", as_json, exit_code=6)
    values = load_projections(projections).df
    name = str(values.loc[p["player_id"], "name"]) if p["player_id"] in values.index else p["player_id"]
    try:
        row = store.record_pick(d, p["overall_pick"], p["player_id"], name, "agent", force=False)
    except store.PickConflict as e:
        fail(f"txn verified but the board disagrees: {e} — fix with `draft undo`/`draft pick`", as_json,
             exit_code=2, code="conflict", draft_id=d["draft_id"], txn=txn)
    emit({"ok": True, "draft_id": d["draft_id"], "txn": txn, "pick": row, "already_verified": already_verified},
         as_json, f"verified #{p['overall_pick']} {name}" + (" (txn was already verified)" if already_verified else ""))


def _str(x: object) -> str:
    return "" if x is None or x != x else str(x)
