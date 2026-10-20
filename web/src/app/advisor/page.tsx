import type { Metadata } from "next";
import { loadContent, MarkdownDoc } from "@/components/MarkdownDoc";

export const dynamic = "force-static";
export const metadata: Metadata = {
  title: "Advisor instructions",
  description: "Instructions for the independent read-only advisor agent: what it can read, the evidence standard and the suggestion API.",
};

/** Same text as docs/integrations/advisor.md (source: web/src/content/advisor.md; a test keeps them identical). */
export default function AdvisorInstructionsPage() {
  return <MarkdownDoc md={loadContent("advisor.md")} />;
}
