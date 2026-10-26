// Web Push for new pending approvals: pure selection/formatting/validation (unit-tested by `npm test`,
// tests/push.test.mjs). The server side (web-push sends, Supabase reads/writes) is in push.ts.
// See docs/runbooks/dashboard.md "Notifications".

/** The approval columns the notifier reads. */
export interface PushApproval {
  approval_id: string;
  action_type: string | null;
  summary: string | null;
  payload: unknown;
  status: string;
  requested_at: string | null;
  expires_at: string | null;
}

/** Only approvals requested within this window are notified: enabling push (or deploying it) never floods the phone
 *  with an old backlog, and a missed trigger is still caught up by the next call within a day. */
export const NOTIFY_WINDOW_MS = 24 * 3600 * 1000;
/** Upper bound per call (a burst of approvals still notifies each, a runaway insert loop doesn't spam). */
export const MAX_PER_CALL = 10;
export const BODY_MAX = 100;

/** Pending, unexpired, recent approvals not notified yet, oldest first. */
export function selectToNotify(rows: PushApproval[], sent: ReadonlySet<string>, nowMs: number): PushApproval[] {
  return rows
    .filter((a) => {
      if (a.status !== "pending" || sent.has(a.approval_id)) return false;
      const req = Date.parse(a.requested_at ?? "");
      if (!Number.isFinite(req) || nowMs - req > NOTIFY_WINDOW_MS) return false;
      const exp = a.expires_at ? Date.parse(a.expires_at) : NaN;
      return !(Number.isFinite(exp) && exp <= nowMs);
    })
    .sort((x, y) => (x.requested_at ?? "").localeCompare(y.requested_at ?? ""))
    .slice(0, MAX_PER_CALL);
}

export function truncate(s: string, max: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length <= max ? t : `${t.slice(0, max - 1).trimEnd()}…`;
}

const ACTION_LABEL: Record<string, string> = { drop: "Drop", add_drop: "Add/drop", trade: "Trade" };

export interface PushMessage {
  title: string;
  body: string;
  url: string; // same-origin path the service worker opens on tap
  tag: string; // one notification per approval (a re-send replaces, never duplicates)
}

/** Notification for one approval. Every approval has an anchor `#<approval_id>` on /inbox (Decisions.tsx), also when
 *  it sits inside an advice card, so the link always lands on the card. */
export function notificationFor(a: PushApproval): PushMessage {
  const kind = ACTION_LABEL[a.action_type ?? ""] ?? "Approval";
  return {
    title: `${kind}: needs your yes`,
    body: truncate(a.summary || a.approval_id, BODY_MAX),
    url: `/inbox#${encodeURIComponent(a.approval_id)}`,
    tag: `approval-${a.approval_id}`,
  };
}

export interface PushSubscriptionRow {
  endpoint: string;
  p256dh: string;
  auth: string;
}

const B64URL = /^[A-Za-z0-9_-]+=*$/;

/** Validate a browser `PushSubscription.toJSON()`; only https endpoints with base64url keys are stored. */
export function parseSubscription(v: unknown): PushSubscriptionRow | null {
  if (!v || typeof v !== "object") return null;
  const o = v as { endpoint?: unknown; keys?: { p256dh?: unknown; auth?: unknown } };
  const endpoint = typeof o.endpoint === "string" ? o.endpoint : "";
  const p256dh = typeof o.keys?.p256dh === "string" ? o.keys.p256dh : "";
  const auth = typeof o.keys?.auth === "string" ? o.keys.auth : "";
  let url: URL;
  try {
    url = new URL(endpoint);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || endpoint.length > 1000) return null;
  if (!B64URL.test(p256dh) || !B64URL.test(auth) || p256dh.length > 200 || auth.length > 100) return null;
  return { endpoint, p256dh, auth };
}

/** The push service says this subscription is permanently gone: delete it. */
export function isGone(statusCode: number | undefined): boolean {
  return statusCode === 404 || statusCode === 410;
}
