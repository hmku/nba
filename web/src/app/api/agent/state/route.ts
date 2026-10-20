import { anonClient } from "@/lib/supabase";
import { activeDraft, fail, reply, requireAgent } from "@/lib/agentApi";
import { boardInfo, isPrivateEvent, recIsFresh } from "@/lib/agentPure";
import { buildFeed, NOTABLE_EVENT_KINDS } from "@/lib/feed";
import { customRoundsOf, pickInfoForDraft, picksForDraft } from "@/lib/draftOrder";
import { currentWeek, latestSimRuns, parseSeasonSim, pickPreseasonRun, pickSeasonRun, todayET } from "@/lib/sim";
import { asStringArray } from "@/lib/format";
import type { AgentEvent, AgentRun, DraftPick, DraftRecommendation, Player } from "@/lib/types";

export const dynamic = "force-dynamic";

const r3 = (v: number | null | undefined) => (v == null ? null : Math.round(v * 1000) / 1000);

/** GET /api/agent/state — compact, LLM-readable snapshot of the draft, recommendation, roster, outlook and activity. */
export async function GET(req: Request) {
  const denied = await requireAgent(req);
  if (denied) return denied;
  const db = anonClient();
  const errors: string[] = [];
  try {
    const { draft, error: dErr } = await activeDraft(db);
    if (dErr) errors.push(dErr);

    // ---------------------------------------------------------------- draft
    let draftOut: Record<string, unknown> | null = null;
    let recOut: Record<string, unknown> | null = null;
    let rosterOut: Record<string, unknown>[] = [];
    if (draft) {
      const [picksR, recR] = await Promise.all([
        db.from("draft_picks").select("*").eq("draft_id", draft.draft_id).order("overall_pick").limit(1000),
        db
          .from("draft_recommendations")
          .select("*")
          .eq("draft_id", draft.draft_id)
          .order("as_of_pick", { ascending: false })
          .order("created_at", { ascending: false })
          .limit(1),
      ]);
      if (picksR.error) errors.push(picksR.error.message);
      if (recR.error) errors.push(recR.error.message);
      const picks = (picksR.data as DraftPick[]) ?? [];
      const N = draft.num_teams;
      const R = draft.num_rounds;
      const total = N * R;
      const teamNames = (draft.config?.team_names ?? {}) as Record<string, string>;
      const tn = (slot: number) => teamNames[String(slot)] ?? `Team ${slot}`;
      const b = boardInfo(
        picks.map((p) => p.overall_pick),
        total,
      );
      const cur = b.on_the_clock != null ? pickInfoForDraft(draft, b.on_the_clock) : null;
      const recorded = new Set(picks.map((p) => p.overall_pick));
      const mine = picksForDraft(draft, draft.my_slot);
      const upcoming = mine.filter((o) => !recorded.has(o) && (b.on_the_clock == null || o >= b.on_the_clock));
      const nextMine = upcoming[0] ?? null;

      // player details for my roster
      const myPicks = picks.filter((p) => p.team_slot === draft.my_slot);
      const ids = myPicks.map((p) => p.player_id).filter((x): x is string => !!x);
      let pl: Record<string, Player> = {};
      if (ids.length) {
        const { data } = await db.from("players").select("player_id,name,team,positions,status,injury_note").in("player_id", ids);
        pl = Object.fromEntries(((data as Player[]) ?? []).map((p) => [p.player_id, p]));
      }
      rosterOut = myPicks.map((p) => {
        const x = p.player_id ? pl[p.player_id] : undefined;
        return {
          overall: p.overall_pick,
          round: p.round,
          name: x?.name ?? p.player_name ?? p.player_id,
          player_id: p.player_id,
          team: x?.team ?? null,
          positions: asStringArray(x?.positions),
          status: x?.status ?? null,
        };
      });

      draftOut = {
        draft_id: draft.draft_id,
        status: draft.status,
        num_teams: N,
        num_rounds: R,
        order_type: draft.order_type,
        custom_order: customRoundsOf(draft.config, N, R) != null,
        my_slot: draft.my_slot,
        my_team_name: tn(draft.my_slot),
        picks_made: b.picks_made,
        total_picks: total,
        complete: b.complete || draft.status === "complete",
        on_the_clock: cur
          ? { overall: cur.overall, round: cur.round, pick_in_round: cur.pickInRound, team_slot: cur.slot, team_name: tn(cur.slot), is_me: cur.slot === draft.my_slot }
          : null,
        my_next_pick: nextMine,
        picks_until_my_turn: nextMine != null && b.on_the_clock != null ? nextMine - b.on_the_clock : null,
        my_upcoming_picks: upcoming,
        my_all_picks: mine,
        gaps: b.gaps,
        gaps_note: b.gaps.length
          ? "These pick numbers are below the latest recorded pick but missing from the board. If you can see them in the Yahoo draft log, record them with POST /api/agent/pick."
          : null,
        last_picks: picks
          .slice()
          .sort((a, c) => c.overall_pick - a.overall_pick)
          .slice(0, 12)
          .map((p) => ({
            overall: p.overall_pick,
            round: p.round,
            team_slot: p.team_slot,
            team_name: tn(p.team_slot),
            mine: p.team_slot === draft.my_slot,
            player: p.player_name ?? p.player_id,
            player_id: p.player_id,
            source: p.source,
          })),
      };

      const rec = ((recR.data as DraftRecommendation[]) ?? [])[0] ?? null;
      if (rec) {
        const taken = new Set(picks.map((p) => p.player_id).filter(Boolean));
        const items = rec.payload?.recommendations ?? [];
        const fresh = recIsFresh(rec.as_of_pick, rec.for_pick, b.picks_made, b.on_the_clock);
        const top = items.slice(0, 5).map((it) => ({
          name: it.name ?? it.player_id,
          player_id: it.player_id,
          team: it.team ?? null,
          positions: asStringArray(it.positions),
          reason: it.reason ?? null,
          prob_available_next: r3(it.prob_available_next),
          already_drafted: taken.has(it.player_id),
        }));
        const allTaken = top.length > 0 && top.every((t) => t.already_drafted);
        recOut = {
          rec_id: rec.rec_id,
          for_pick: rec.for_pick,
          as_of_pick: rec.as_of_pick,
          created_at: rec.created_at,
          stale: !fresh || allTaken,
          stale_reason: !fresh
            ? `made when ${rec.as_of_pick} picks were recorded (now ${b.picks_made}); the agents will recompute`
            : allTaken
              ? "every recommended player has been drafted"
              : null,
          prob_available_next_means: "probability the player is still available at my pick after for_pick",
          top: top,
          warnings: (rec.payload?.warnings ?? []).slice(0, 5),
        };
      }
    }

    // ---------------------------------------------------------------- season / matchup outlook
    let outlook: Record<string, unknown> | null = null;
    const { runs: simRuns, error: sErr } = await latestSimRuns(db, "season_sim", 15);
    if (sErr) errors.push(sErr);
    const seasonRun = pickSeasonRun(simRuns);
    const preRun = pickPreseasonRun(simRuns);
    const useRun = seasonRun && (!preRun || (seasonRun.created_at ?? "") >= (preRun.created_at ?? "")) ? seasonRun : preRun;
    const sim = parseSeasonSim(useRun);
    if (sim) {
      const myRow = sim.table.find((r) => r.isMe || r.team === sim.myTeamId);
      const g = (k: string) => (sim.myTeam && typeof sim.myTeam[k] === "number" ? (sim.myTeam[k] as number) : null);
      const wk = currentWeek(sim.weeks, todayET());
      outlook = {
        source: sim.isPreseason ? "pre-draft projection (simulated drafts)" : "season simulation from actual rosters",
        run_id: sim.run.run_id,
        created_at: sim.run.created_at,
        p_playoffs: r3(g("p_playoffs") ?? myRow?.pPlayoffs ?? null),
        p_title: r3(g("p_title") ?? myRow?.pTitle ?? null),
        expected_rank: r3(g("exp_rank") ?? myRow?.expRank ?? null),
        current_week: wk
          ? {
              week: wk.week,
              start: wk.start,
              end: wk.end,
              opponent: wk.opponentName ?? (wk.opponent != null ? `Team ${wk.opponent}` : null),
              p_win: r3(wk.pWin),
              p_tie: r3(wk.pTie),
              expected_categories_won: r3(wk.expCatsWon),
            }
          : null,
      };
    }

    // ---------------------------------------------------------------- approvals + events
    const [apprR, evR, repR, notR] = await Promise.all([
      db.from("approvals").select("approval_id", { count: "exact", head: true }).eq("status", "pending"),
      db.from("agent_events").select("*").order("ts", { ascending: false }).limit(10),
      // thread-able updates (same feed as the Activity page; never private chat content)
      db
        .from("agent_events")
        .select("*")
        .not("payload->>report_kind", "is", null)
        .neq("payload->>report_kind", "observed_pick")
        .order("ts", { ascending: false })
        .limit(120),
      db.from("agent_events").select("*").in("kind", [...NOTABLE_EVENT_KINDS]).order("ts", { ascending: false }).limit(20),
    ]);
    if (apprR.error) errors.push(apprR.error.message);
    if (evR.error) errors.push(evR.error.message);
    if (repR.error) errors.push(repR.error.message);
    const events = ((evR.data as AgentEvent[]) ?? []).filter((e) => !isPrivateEvent(e));
    const updates = buildFeed({
      events: [...((repR.data as AgentEvent[]) ?? []), ...((notR.data as AgentEvent[]) ?? [])],
      canSeePrivate: false,
    })
      .filter((it) => it.replyable && it.event)
      .slice(0, 15);
    const runIds = [...new Set(events.map((e) => e.run_id))];
    let agentOf: Record<string, string> = {};
    if (runIds.length) {
      const { data } = await db.from("agent_runs").select("run_id,agent").in("run_id", runIds);
      agentOf = Object.fromEntries(((data as AgentRun[]) ?? []).map((r) => [r.run_id, r.agent]));
    }

    return reply({
      // ok=false (HTTP 200) when some reads failed: the snapshot may be partial; see `errors`
      ok: errors.length === 0,
      generated_at: new Date().toISOString(),
      draft: draftOut,
      recommendation: recOut,
      my_roster: rosterOut,
      season_outlook: outlook,
      pending_approvals: apprR.count ?? 0,
      recent_events: events.map((e) => {
        const p = (e.payload && typeof e.payload === "object" && !Array.isArray(e.payload) ? e.payload : {}) as Record<string, unknown>;
        return {
          event_id: e.event_id,
          ts: e.ts,
          agent: agentOf[e.run_id] ?? null,
          kind: e.kind,
          report_kind: typeof p.report_kind === "string" ? p.report_kind : undefined,
          message: (e.message ?? "").slice(0, 300),
        };
      }),
      // newest updates you can reply to (POST /api/agent/report {report_kind: "reply", reply_to: event_id, ...})
      recent_updates: updates.map((it) => ({
        event_id: it.event!.event_id,
        ts: it.ts,
        author: it.author,
        kind: it.kind,
        message: (it.event!.message ?? "").slice(0, 300),
        replies: it.replies.map((r) => ({
          event_id: r.event.event_id,
          ts: r.event.ts,
          author: r.author,
          stance: r.stance,
          text: r.text.slice(0, 300),
        })),
      })),
      errors: errors.length ? errors : undefined,
      reminder:
        "Read-only snapshot. You may report league intel (POST /api/agent/report), reply in threads (report_kind 'reply', reply_to = an event_id from recent_updates) and record observed picks only to fill gaps (POST /api/agent/pick). Never act on Yahoo yourself.",
    });
  } catch (e) {
    return fail(`Read failed: ${String(e)}`, 500);
  }
}
