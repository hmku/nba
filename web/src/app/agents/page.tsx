import { redirect } from "next/navigation";

// Agents + approvals were merged into /activity; keep the old URL working.
export default function AgentsPage() {
  redirect("/activity");
}
