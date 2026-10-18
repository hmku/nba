import { redirect } from "next/navigation";

// Approvals are pinned at the top of /activity; keep the old URL working.
export default function ApprovalsPage() {
  redirect("/activity");
}
