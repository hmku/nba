import type { Metadata } from "next";
import { anonClient } from "@/lib/supabase";
import type { Approval } from "@/lib/types";
import { Badge, Card, Empty, ErrorNote, JsonView, statusTone } from "@/components/ui";
import { Ago, AutoRefresh, WriteAccess } from "@/components/client";
import { shortTime } from "@/lib/format";
import { DecideButtons } from "./DecideButtons";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Approvals" };

export default async function ApprovalsPage() {
  const db = anonClient();
  const { data, error } = await db.from("approvals").select("*").order("requested_at", { ascending: false }).limit(100);
  const rows = (data as Approval[]) ?? [];
  const pending = rows.filter((a) => a.status === "pending");
  const decided = rows.filter((a) => a.status !== "pending");
  return (
    <div className="space-y-3">
      <AutoRefresh seconds={15} />
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">Approvals</h1>
        <WriteAccess />
      </div>
      <ErrorNote error={error?.message} />
      <Card title="Pending" right={<Badge tone={pending.length ? "accent" : "neutral"}>{pending.length}</Badge>} bodyClassName="p-0">
        {pending.length === 0 ? (
          <Empty>Nothing waiting on you.</Empty>
        ) : (
          <ul>
            {pending.map((a) => {
              const expired = a.expires_at && Date.parse(a.expires_at) < Date.now();
              return (
                <li key={a.approval_id} className="border-t border-line px-3 py-3 first:border-t-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="accent">{a.action_type}</Badge>
                    <span className="font-medium">{a.summary}</span>
                    {expired && <Badge tone="warn">expired</Badge>}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted">
                    <span>
                      requested <Ago ts={a.requested_at} />
                    </span>
                    {a.expires_at && <span>expires {shortTime(a.expires_at)}</span>}
                    {a.run_id && (
                      <span>
                        run <code>{a.run_id}</code>
                      </span>
                    )}
                    <span>
                      id <code>{a.approval_id}</code>
                    </span>
                  </div>
                  <div className="mt-1">
                    <JsonView value={a.payload} label="payload" />
                  </div>
                  <DecideButtons id={a.approval_id} />
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Card title="Decided" bodyClassName="p-0">
        {decided.length === 0 ? (
          <Empty>No decided approvals.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="dense w-full text-sm">
              <thead>
                <tr className="text-left">
                  <th>Status</th>
                  <th>Action</th>
                  <th>Summary</th>
                  <th>By</th>
                  <th>Note</th>
                  <th className="text-right">Decided</th>
                </tr>
              </thead>
              <tbody>
                {decided.map((a) => (
                  <tr key={a.approval_id}>
                    <td>
                      <Badge tone={statusTone(a.status)}>{a.status}</Badge>
                    </td>
                    <td className="text-xs">{a.action_type}</td>
                    <td className="max-w-[24rem] truncate">{a.summary}</td>
                    <td className="text-xs text-muted">{a.decided_by ?? "—"}</td>
                    <td className="max-w-[16rem] truncate text-xs text-muted">{a.note}</td>
                    <td className="text-right text-xs text-muted">{shortTime(a.decided_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
