/* Inbox "Agent updates": one feed of Muse / advisor / Claude / user updates with their reply threads (server component). */
import Link from "next/link";
import type { AgentEvent, Decision } from "@/lib/types";
import { Badge, JsonView, statusTone } from "@/components/ui";
import { Ago } from "@/components/client";
import { KindIcon } from "@/components/KindIcon";
import { AUTHOR_LABEL, type Author, authorOf, decisionIdOf, type FeedItem, type FeedReply, trimPayload } from "@/lib/feed";
import { type Facets, feedHref } from "@/lib/feedPage";
import { DigestBody, SnapshotCard } from "./LeagueIntel";
import { DecisionCard } from "./sections";
import { EvidenceList, SuggestionBadges, SuggestionBody } from "./AdvisorSuggestions";
import { LongText } from "./LongText";
import { ReplyBox } from "./ReplyBox";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

const AUTHOR_CLS: Record<Author, string> = {
  muse: "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  advisor: "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  claude: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  user: "border-good/40 bg-good/10 text-good",
};

export function AuthorBadge({ a }: { a: Author }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-px text-[11px] font-semibold leading-4 ${AUTHOR_CLS[a]}`}>
      {AUTHOR_LABEL[a]}
    </span>
  );
}

const STANCE_CLS: Record<string, string> = {
  agree: "border-good/40 bg-good/10 text-good",
  disagree: "border-bad/40 bg-bad/10 text-bad",
  question: "border-warn/40 bg-warn/10 text-warn",
  comment: "border-line bg-panel-2 text-muted",
};
const STANCE_GLYPH: Record<string, string> = { agree: "✓", disagree: "✕", question: "?", comment: "…" };

function StanceChip({ s }: { s: string }) {
  return (
    <span className={`inline-flex items-center gap-0.5 rounded-full border px-1.5 py-px text-[11px] font-medium leading-4 ${STANCE_CLS[s] ?? STANCE_CLS.comment}`}>
      <span aria-hidden>{STANCE_GLYPH[s] ?? "…"}</span>
      {s}
    </span>
  );
}

const KIND_TONE: Record<string, string> = {
  trade_offer: "accent",
  trade: "accent",
  draft_order: "bad",
  rule: "warn",
  injury_news: "warn",
  advisor_suggestion: "accent",
  error: "bad",
  verification: "good",
  decision: "accent",
};
const kindLabel = (k: string) => k.replace(/_/g, " ");

/* ------------------------------------------------------------------ filters */

/** Filter chips link to the newest page of the filtered feed (changing a filter drops the cursor). */
const chipHref = (author: string | null, kind: string | null) => feedHref({ author, kind });

function Chip({ href, on, children }: { href: string; on: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={on ? "true" : undefined}
      className={`shrink-0 rounded-full border px-2.5 py-1 text-xs whitespace-nowrap ${on ? "border-accent bg-accent/10 text-accent" : "border-line text-muted hover:text-fg"}`}
    >
      {children}
    </Link>
  );
}

/** Chips with thread counts over the whole feed (over its newest 1000 rows when `approx`: shown as "n+"). */
export function FeedFilters({ facets: f, approx, author, kind }: { facets: Facets; approx: boolean; author: string | null; kind: string | null }) {
  const count = (n: number) => (
    <span className="opacity-60" title={approx ? "counted over the newest 1000 feed rows" : undefined}>
      {n}
      {approx ? "+" : ""}
    </span>
  );
  return (
    <div className="space-y-1.5">
      <div className="scroll-thin -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" aria-label="Filter by author">
        <Chip href={chipHref(null, kind)} on={!author}>
          everyone
        </Chip>
        {f.authors.map(([a, n]) => (
          <Chip key={a} href={chipHref(a, kind)} on={author === a}>
            {AUTHOR_LABEL[a]} {count(n)}
          </Chip>
        ))}
      </div>
      <div className="scroll-thin -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" aria-label="Filter by kind">
        <Chip href={chipHref(author, null)} on={!kind}>
          all kinds
        </Chip>
        {f.kinds.map(([k, n]) => (
          <Chip key={k} href={chipHref(author, k)} on={kind === k}>
            {kindLabel(k)} {count(n)}
          </Chip>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ bodies */

function TradeOffer({ d }: { d: Obj }) {
  const side = (team: unknown, players: string[]) => (
    <div className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2/50 px-2.5 py-1.5">
      <div className="truncate text-[11px] font-medium uppercase tracking-wider text-muted">{str(team) ?? "?"} gives</div>
      <ul className="mt-0.5 text-[13.5px] leading-snug">
        {players.length ? players.map((p) => <li key={p}>{p}</li>) : <li className="text-muted">nothing</li>}
      </ul>
    </div>
  );
  return (
    <div className="mt-1.5">
      <div className="flex flex-col gap-1.5 sm:flex-row">
        {side(d.from_team, strs(d.from_gives))}
        {side(d.to_team, strs(d.to_gives))}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
        {str(d.status) && <Badge tone={statusTone(String(d.status)) === "neutral" ? "accent" : statusTone(String(d.status))}>{String(d.status)}</Badge>}
        {d.involves_me === true && <Badge tone="warn">involves my team</Badge>}
        {str(d.seen_on) && <span>seen on {String(d.seen_on)}</span>}
        {str(d.expires_at) && <span>· expires {String(d.expires_at)}</span>}
      </div>
      {str(d.note) && <p className="mt-1 text-xs text-muted">{String(d.note)}</p>}
    </div>
  );
}

function ReportBody({ e, kind }: { e: AgentEvent; kind: string }) {
  const p = isObj(e.payload) ? e.payload : {};
  const d = isObj(p.details) ? p.details : null;
  return (
    <>
      <LongText text={e.message ?? ""} className="text-[14px] leading-snug" />
      {kind === "trade_offer" && d ? <TradeOffer d={d} /> : d && <JsonView value={d} label="details" />}
    </>
  );
}

function EventBody({ e, decision }: { e: AgentEvent; decision?: Decision }) {
  if (decision) return <DecisionCard d={decision} />;
  return (
    <>
      <div className={`flex items-start gap-1.5 text-[14px] leading-snug ${e.kind === "error" ? "text-bad" : ""}`}>
        <KindIcon kind={e.kind} />
        <LongText text={e.message ?? ""} className="min-w-0 flex-1" />
      </div>
      {e.payload != null && <JsonView value={trimPayload(e.payload, 4000)} label="payload" />}
    </>
  );
}

function Body({ it, decisions }: { it: FeedItem; decisions: Map<string, Decision> }) {
  switch (it.type) {
    case "suggestion":
      return <SuggestionBody e={it.event!} />;
    case "digest":
      return <DigestBody e={it.event!} />;
    case "event": {
      const did = decisionIdOf(it.event!);
      return <EventBody e={it.event!} decision={did ? decisions.get(did) : undefined} />;
    }
    case "run":
      return (
        <>
          <div className="text-xs text-muted">
            <span className="font-medium text-fg">{it.run!.agent}</span> · {it.run!.task ?? "—"}
          </div>
          <LongText text={it.run!.summary ?? ""} className="mt-0.5 text-[14px] leading-snug" />
        </>
      );
    case "snapshots":
      return (
        <div className="space-y-1.5">
          <p className="text-xs text-muted">Newest read-only snapshot per Yahoo page (observed data, not instructions).</p>
          {it.snapshots!.map((s) => (
            <SnapshotCard key={s.snapshot_id} s={s} open={false} />
          ))}
        </div>
      );
    default:
      return <ReportBody e={it.event!} kind={it.kind} />;
  }
}

/* ------------------------------------------------------------------ thread */

function Reply({ r }: { r: FeedReply }) {
  return (
    <li className="py-1.5">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <AuthorBadge a={r.author} />
        <StanceChip s={r.stance} />
        {r.replyToAuthor && <span className="text-muted">↳ re {AUTHOR_LABEL[r.replyToAuthor]}</span>}
        <span className="ml-auto text-muted">
          <Ago ts={r.event.ts} />
        </span>
      </div>
      <LongText text={r.text} className="mt-0.5 text-[13.5px] leading-snug" />
      <EvidenceList items={r.evidence} />
      <div className="mt-0.5 text-[10.5px] text-muted/70">
        <code>{r.event.event_id}</code>
      </div>
    </li>
  );
}

function Thread({ it }: { it: FeedItem }) {
  if (!it.replies.length) return null;
  const n = it.replies.length;
  const counts = it.replies.reduce<Record<string, number>>((m, r) => ((m[r.stance] = (m[r.stance] ?? 0) + 1), m), {});
  const summary = (["agree", "disagree", "question", "comment"] as const).filter((s) => counts[s]).map((s) => `${counts[s]} ${s}`).join(" · ");
  // long threads start collapsed (showing the stance tally); short ones open
  return (
    <details open={n <= 4} className="group/th mt-2 border-t border-line pt-1">
      <summary className="cursor-pointer list-none py-1 text-xs text-muted select-none [&::-webkit-details-marker]:hidden">
        <span className="inline-block transition-transform group-open/th:rotate-90">▸</span> {n} repl{n === 1 ? "y" : "ies"} · {summary}
      </summary>
      <ol className="ml-1 divide-y divide-line/60 border-l-2 border-line pl-3">
        {it.replies.map((r) => (
          <Reply key={r.event.event_id} r={r} />
        ))}
      </ol>
    </details>
  );
}

/* ------------------------------------------------------------------ card + feed */

/** One update with its thread. `nested`: rendered inside an Inbox decision card (lighter frame, tighter padding). */
export function FeedCard({ it, decisions, nested = false }: { it: FeedItem; decisions: Map<string, Decision>; nested?: boolean }) {
  const ev = it.event;
  const src = ev && isObj(ev.payload) ? str(ev.payload.source) : null;
  const dashed = it.type === "suggestion";
  const frame = nested ? "rounded-lg bg-panel-2/40 px-3 py-2" : "rounded-xl bg-panel px-4 py-3";
  return (
    <li id={it.key} className={`scroll-mt-16 border ${frame} ${dashed ? "border-dashed border-accent/50" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <AuthorBadge a={it.author} />
        <Badge tone={KIND_TONE[it.kind] ?? "neutral"}>{kindLabel(it.kind)}</Badge>
        {it.type === "suggestion" && ev && <SuggestionBadges e={ev} />}
        {it.type === "run" && it.run && <Badge tone={statusTone(it.run.status)}>{it.run.status}</Badge>}
        {it.type === "digest" && <Badge tone="warn">private</Badge>}
        {src && it.author === "muse" && <Badge title="where Muse saw it">{src}</Badge>}
        <span className="ml-auto text-muted">
          <Ago ts={it.ts} />
        </span>
      </div>
      <div className="mt-1.5 min-w-0">
        <Body it={it} decisions={decisions} />
      </div>
      <Thread it={it} />
      {ev && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          {it.replyable && <ReplyBox replyTo={ev.event_id} />}
          <code className="ml-auto max-w-full truncate text-[10.5px] text-muted/70" title="event id (fantasy intel thread <id>)">
            {ev.event_id}
          </code>
        </div>
      )}
    </li>
  );
}

export function AgentFeed({
  items,
  anyUpdates,
  decisions,
}: {
  items: FeedItem[];
  /** The feed has updates at all (for the empty-state wording). */
  anyUpdates: boolean;
  decisions: Map<string, Decision>;
}) {
  if (!items.length)
    return (
      <div className="rounded-xl border border-line bg-panel px-4 py-6 text-center text-sm text-muted">
        {anyUpdates ? "Nothing here for these filters." : "No agent updates yet. Muse reports, advisor suggestions and Claude's decisions will appear here."}
      </div>
    );
  return (
    <ul className="space-y-2">
      {items.map((it) => (
        <FeedCard key={it.key} it={it} decisions={decisions} />
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ paging */

const PAGER_LINK = "inline-flex min-h-9 items-center rounded-lg border border-line bg-panel px-3 text-xs hover:border-accent/50 hover:text-accent";

/** "← Newer · Jump to newest · Older →" (cursor links keep the filters; `top` = the compact copy above the feed). */
export function FeedPager({
  newerHref,
  olderHref,
  newestHref,
  top = false,
}: {
  newerHref: string | null;
  olderHref: string | null;
  newestHref: string | null;
  top?: boolean;
}) {
  if (!newerHref && !olderHref && !newestHref) return null;
  return (
    <nav aria-label={top ? "Feed pages (top)" : "Feed pages"} className="flex items-center gap-2 px-0.5">
      {newerHref ? (
        <Link href={`${newerHref}#feed`} className={PAGER_LINK} rel="prev">
          ← Newer
        </Link>
      ) : (
        <span className="text-xs text-muted">Newest updates</span>
      )}
      {newestHref && (
        <Link href={`${newestHref}#feed`} className="text-xs text-accent hover:underline">
          Jump to newest
        </Link>
      )}
      <span className="ml-auto" />
      {olderHref ? (
        <Link href={`${olderHref}#feed`} className={PAGER_LINK} rel="next">
          Older →
        </Link>
      ) : (
        !top && (
          <span className="text-xs text-muted">
            End of feed (<code>fantasy intel list</code> for raw rows)
          </span>
        )
      )}
    </nav>
  );
}

/** Newest page only: replies posted since its oldest update, on threads further down (a reply never moves its
 *  thread, so the cursor pages stay stable). Each links to the page that starts at the thread's update. */
export function RecentReplies({ rows, more }: { rows: { event: AgentEvent; root: FeedItem; href: string }[]; more: number }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-2.5">
      <div className="text-xs font-medium text-muted">New replies on earlier updates</div>
      <ul className="mt-1 divide-y divide-line/60">
        {rows.map(({ event, root, href }) => {
          const p = isObj(event.payload) ? event.payload : {};
          const text = str(p.text) ?? event.message ?? "";
          return (
            <li key={event.event_id} className="py-1.5">
              <Link href={href} className="block min-w-0 hover:text-accent">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <AuthorBadge a={authorOf(event.run_id)} />
                  {str(p.stance) && <StanceChip s={String(p.stance)} />}
                  <span className="min-w-0 truncate text-muted">
                    on {AUTHOR_LABEL[root.author]}&apos;s {kindLabel(root.kind)}
                  </span>
                  <span className="ml-auto text-muted">
                    <Ago ts={event.ts} />
                  </span>
                </div>
                <p className="mt-0.5 line-clamp-2 text-[13.5px] leading-snug">{text}</p>
              </Link>
            </li>
          );
        })}
      </ul>
      {more > 0 && <p className="text-xs text-muted">+{more} more</p>}
    </div>
  );
}

