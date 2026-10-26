import "server-only";
import webpush from "web-push";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  isGone,
  NOTIFY_WINDOW_MS,
  notificationFor,
  selectToNotify,
  type PushApproval,
  type PushMessage,
  type PushSubscriptionRow,
} from "./pushPure";

/** VAPID config from the server env (VAPID_PRIVATE_KEY never leaves the server). */
export function vapid(): { publicKey: string; privateKey: string; subject: string } | null {
  const publicKey = process.env.VAPID_PUBLIC_KEY?.trim();
  const privateKey = process.env.VAPID_PRIVATE_KEY?.trim();
  const subject = process.env.VAPID_SUBJECT?.trim();
  if (!publicKey || !privateKey || !subject) return null;
  return { publicKey, privateKey, subject };
}

export interface SendResult {
  sent: number;
  failed: number;
  removed: number;
}

/** Send one message to each subscription; drop subscriptions the push service reports gone (404/410). */
export async function sendTo(db: SupabaseClient, subs: PushSubscriptionRow[], msg: PushMessage): Promise<SendResult> {
  const v = vapid();
  if (!v) throw new Error("VAPID keys not configured");
  const res: SendResult = { sent: 0, failed: 0, removed: 0 };
  const now = new Date().toISOString();
  await Promise.all(
    subs.map(async (s) => {
      try {
        await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, JSON.stringify(msg), {
          vapidDetails: v,
          TTL: 6 * 3600,
          urgency: "high",
          timeout: 10_000,
        });
        res.sent++;
        await db.from("push_subscriptions").update({ last_ok_at: now, last_error: null }).eq("endpoint", s.endpoint);
      } catch (e) {
        const code = (e as { statusCode?: number }).statusCode;
        if (isGone(code)) {
          res.removed++;
          await db.from("push_subscriptions").delete().eq("endpoint", s.endpoint);
        } else {
          res.failed++;
          const why = `${code ?? "error"}: ${String((e as Error).message ?? e).slice(0, 200)}`;
          await db.from("push_subscriptions").update({ last_error: why }).eq("endpoint", s.endpoint);
        }
      }
    }),
  );
  return res;
}

export async function loadSubscriptions(db: SupabaseClient): Promise<{ subs: PushSubscriptionRow[]; error?: string }> {
  const { data, error } = await db.from("push_subscriptions").select("endpoint,p256dh,auth");
  return error ? { subs: [], error: error.message } : { subs: (data ?? []) as PushSubscriptionRow[] };
}

export interface NotifyResult {
  notified: { approval_id: string; recipients: number; failures: number; removed: number }[];
  subscriptions: number;
  error?: string;
}

/**
 * Notify every pending, unexpired approval requested in the last 24 h that has no `push_sent` row yet. Each approval is
 * claimed (insert ... on conflict do nothing) BEFORE sending, so concurrent or repeated calls (webhook retries, a manual
 * curl) notify it at most once. An approval found while nobody is subscribed is still claimed (no backlog later).
 */
export async function notifyPending(db: SupabaseClient, nowMs = Date.now()): Promise<NotifyResult> {
  const since = new Date(nowMs - NOTIFY_WINDOW_MS).toISOString();
  const apr = await db
    .from("approvals")
    .select("approval_id,action_type,summary,payload,status,requested_at,expires_at")
    .eq("status", "pending")
    .gte("requested_at", since)
    .order("requested_at", { ascending: true })
    .limit(100);
  if (apr.error) return { notified: [], subscriptions: 0, error: apr.error.message };
  const rows = (apr.data ?? []) as PushApproval[];
  if (!rows.length) return { notified: [], subscriptions: 0 };
  const sentQ = await db
    .from("push_sent")
    .select("approval_id")
    .in(
      "approval_id",
      rows.map((r) => r.approval_id),
    );
  if (sentQ.error) return { notified: [], subscriptions: 0, error: sentQ.error.message };
  const todo = selectToNotify(rows, new Set((sentQ.data ?? []).map((r) => String(r.approval_id))), nowMs);
  if (!todo.length) return { notified: [], subscriptions: 0 };
  const { subs, error } = await loadSubscriptions(db);
  if (error) return { notified: [], subscriptions: 0, error };

  const out: NotifyResult = { notified: [], subscriptions: subs.length };
  for (const a of todo) {
    const claim = await db
      .from("push_sent")
      .upsert({ approval_id: a.approval_id, sent_at: new Date().toISOString(), recipients: 0, failures: 0 }, {
        onConflict: "approval_id",
        ignoreDuplicates: true,
      })
      .select("approval_id");
    if (claim.error) return { ...out, error: claim.error.message };
    if (!claim.data?.length) continue; // another call claimed it first
    const r = subs.length ? await sendTo(db, subs, notificationFor(a)) : { sent: 0, failed: 0, removed: 0 };
    await db.from("push_sent").update({ recipients: r.sent, failures: r.failed }).eq("approval_id", a.approval_id);
    out.notified.push({ approval_id: a.approval_id, recipients: r.sent, failures: r.failed, removed: r.removed });
  }
  return out;
}
