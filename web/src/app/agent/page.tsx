import type { Metadata } from "next";
import { loadContent, MarkdownDoc } from "@/components/MarkdownDoc";

export const dynamic = "force-static";
export const metadata: Metadata = {
  title: "Agent instructions",
  description: "Instructions for external assistants (Meta Muse) using the Fantasy Desk agent API.",
};

/** Same text as docs/integrations/muse.md (source: web/src/content/muse.md; a test keeps them identical). */
export default function AgentInstructionsPage() {
  return <MarkdownDoc md={loadContent("muse.md")} />;
}
