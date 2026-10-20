import type { ReactNode } from "react";
import type { AgentEvent, LeagueSnapshot } from "@/lib/types";
import { Badge, JsonView } from "@/components/ui";
import { Ago } from "@/components/client";
import { catLeader, snapshotLabel } from "@/lib/agentPure";
import { CAT_LABEL } from "@/lib/categories";
import type { CatKey } from "@/lib/types";
import { LongText } from "./LongText";

/** League-intel bodies for the Agent updates feed: Muse's newest Yahoo snapshot per kind + group-chat digests.
 *  Everything here is observed data (Yahoo pages / chat), shown as-is; nothing is acted on from this page. */

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const objs = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const txt = (v: unknown): string => (v == null ? "—" : Array.isArray(v) ? v.map(String).join(", ") : String(v));

/** Badge tone for an injury status (healthy / DTD-GTD / out). */
function injuryTone(s: unknown): string {
  const v = String(s ?? "healthy");
  if (v === "healthy") return "good";
  if (v === "DTD" || v === "GTD") return "warn";
  return "bad";
}

const TH = "px-2 py-1 text-left text-[11px] font-medium uppercase tracking-wider text-muted";
const TD = "px-2 py-1";

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-line">
            {head.map((h) => (
              <th key={h} className={TH}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line/60 first:border-t-0">
              {r.map((c, j) => (
                <td key={j} className={TD}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MyTeam({ p }: { p: Obj }) {
  const players = objs(p.players);
  return (
    <>
      <div className="mb-1 text-xs text-muted">
        {txt(p.team_name)}
        {p.adds_used_this_week != null && <> · adds used this week {txt(p.adds_used_this_week)}</>}
      </div>
      <Table
        head={["slot", "player", "pos", "status"]}
        rows={players.map((x) => [
          <span key="s" className="text-muted">{txt(x.slot)}</span>,
          <span key="n">
            {txt(x.name)}
            {x.injury_note != null && <span className="ml-1 text-xs text-muted">({txt(x.injury_note)})</span>}
          </span>,
          <span key="p" className="text-muted">{txt(x.positions)}</span>,
          <Badge key="b" tone={injuryTone(x.status)}>{txt(x.status ?? "healthy")}</Badge>,
        ])}
      />
    </>
  );
}

function Matchup({ p }: { p: Obj }) {
  const cats = isObj(p.categories) ? Object.entries(p.categories).filter(([, v]) => isObj(v)) : [];
  const gr = isObj(p.games_remaining) ? p.games_remaining : null;
  const cell = (cat: string, mine: boolean, v: Obj) => {
    const lead = catLeader(cat, v.me, v.opp);
    const win = lead === (mine ? "me" : "opp");
    return <span className={`num ${win ? (mine ? "font-semibold text-good" : "font-semibold text-bad") : ""}`}>{txt(mine ? v.me : v.opp)}</span>;
  };
  return (
    <>
      <div className="mb-1 text-xs text-muted">
        vs <span className="text-fg">{txt(p.opponent)}</span>
        {gr && (
          <>
            {" "}
            · games left {txt(gr.me)} – {txt(gr.opp)}
          </>
        )}
      </div>
      <Table
        head={["cat", "me", "opp"]}
        rows={cats.map(([c, v]) => [
          <span key="c" className="text-muted">{CAT_LABEL[c as CatKey] ?? c}</span>,
          cell(c, true, v as Obj),
          cell(c, false, v as Obj),
        ])}
      />
    </>
  );
}

function Standings({ p }: { p: Obj }) {
  return (
    <Table
      head={["#", "team", "record"]}
      rows={objs(p.teams).map((t) => [
        <span key="r" className="num text-muted">{txt(t.rank)}</span>,
        txt(t.team_name),
        <span key="w" className="num">{txt(t.record)}</span>,
      ])}
    />
  );
}

function Transactions({ p }: { p: Obj }) {
  const items = objs(p.items).slice(0, 12);
  if (!items.length) return <div className="text-xs text-muted">No transactions listed.</div>;
  return (
    <ul className="space-y-1 text-[13px]">
      {items.map((t, i) => (
        <li key={i} className="flex flex-wrap items-baseline gap-1.5">
          <Badge tone={t.type === "trade" ? "accent" : "neutral"}>{txt(t.type)}</Badge>
          {t.team != null && <span className="font-medium">{txt(t.team)}</span>}
          <span>{txt(t.players)}</span>
          {t.ts != null && <span className="text-xs text-muted">{txt(t.ts)}</span>}
        </li>
      ))}
    </ul>
  );
}

function Injuries({ p }: { p: Obj }) {
  return (
    <Table
      head={["player", "status", "fantasy team"]}
      rows={objs(p.players).map((x) => [
        <span key="n">
          {txt(x.name)}
          {x.note != null && <span className="ml-1 text-xs text-muted">({txt(x.note)})</span>}
        </span>,
        <Badge key="s" tone={injuryTone(x.status)}>{txt(x.status)}</Badge>,
        <span key="t" className="text-muted">{txt(x.fantasy_team)}</span>,
      ])}
    />
  );
}

function Body({ s }: { s: LeagueSnapshot }) {
  const p = isObj(s.payload) ? (s.payload as Obj) : {};
  switch (s.kind) {
    case "my_team":
      return <MyTeam p={p} />;
    case "matchup":
      return <Matchup p={p} />;
    case "standings":
      return <Standings p={p} />;
    case "transactions":
      return <Transactions p={p} />;
    case "injuries":
      return <Injuries p={p} />;
    default:
      return null;
  }
}

/** Big kinds start expanded; bulky ones (rosters, free agents, settings) stay collapsed. */
const OPEN = new Set(["my_team", "matchup", "standings", "transactions", "injuries"]);

export function SnapshotCard({ s, open }: { s: LeagueSnapshot; open?: boolean }) {
  return (
    <details open={open ?? OPEN.has(s.kind)} className="group rounded-xl border border-line bg-panel">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-4 py-2.5 select-none [&::-webkit-details-marker]:hidden">
        <span className="text-xs text-muted transition-transform group-open:rotate-90">▶</span>
        <span className="text-[14px] font-medium">{snapshotLabel(s.kind)}</span>
        {s.week != null && <Badge>week {s.week}</Badge>}
        <span className="min-w-0 flex-1 truncate text-xs text-muted">{s.summary}</span>
        <span className="shrink-0 text-xs text-muted" title="last seen by Muse">
          <Ago ts={s.last_seen_at ?? s.observed_at ?? s.ts} staleAfter={36 * 3600} />
        </span>
      </summary>
      <div className="space-y-2 border-t border-line px-4 py-3">
        <Body s={s} />
        <div className="flex flex-wrap items-center gap-x-3 text-[11px] text-muted">
          <code>{s.snapshot_id}</code>
          {s.source_url && (
            <a href={s.source_url} className="text-accent" target="_blank" rel="noreferrer">
              Yahoo page
            </a>
          )}
          <JsonView value={s.payload} label="raw payload" />
        </div>
      </div>
    </details>
  );
}

/** One group-chat digest (private: only rendered after the passphrase unlock). */
export function DigestBody({ e }: { e: AgentEvent }) {
  const p = isObj(e.payload) ? (e.payload as Obj) : {};
  const d = isObj(p.details) ? p.details : {};
  const ex = objs(d.excerpts);
  return (
    <div className="text-[13.5px]">
      {(d.period_start != null || d.period_end != null || d.message_count != null) && (
        <div className="mb-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
          {(d.period_start != null || d.period_end != null) && (
            <span>
              covers {txt(d.period_start)} → {txt(d.period_end)}
            </span>
          )}
          {d.message_count != null && <span>· {txt(d.message_count)} msgs</span>}
        </div>
      )}
      <LongText text={e.message ?? ""} className="leading-snug" />
      {ex.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer select-none text-xs text-accent">
            {ex.length} excerpt{ex.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-1 space-y-1 text-xs">
            {ex.map((x, i) => (
              <li key={i} className="rounded border border-line bg-panel-2 px-2 py-1">
                <span className="font-medium">{txt(x.author ?? "?")}</span>
                {x.at != null && <span className="ml-1 text-muted">{txt(x.at)}</span>}
                <div className="whitespace-pre-wrap break-words">{txt(x.text)}</div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

