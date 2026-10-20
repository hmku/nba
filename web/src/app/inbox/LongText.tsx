import { splitLong } from "@/lib/feed";

/** Text that collapses after ~420 chars behind "more" (native <details>: works in server components, no JS). */
export function LongText({ text, className = "" }: { text: string; className?: string }) {
  const { head, rest } = splitLong(text);
  if (rest == null) return <div className={`whitespace-pre-wrap break-words ${className}`}>{text}</div>;
  return (
    <details className={`group/lt ${className}`}>
      <summary className="cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
        <span className="whitespace-pre-wrap break-words group-open/lt:hidden">
          {head}… <span className="text-accent">more</span>
        </span>
        <span className="hidden text-xs text-accent group-open/lt:inline">less</span>
      </summary>
      <div className="whitespace-pre-wrap break-words">{text}</div>
    </details>
  );
}
