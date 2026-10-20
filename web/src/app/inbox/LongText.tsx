/** Chars above which a text may run past 2 lines on a phone (then it gets a "more" toggle). */
const CLAMP_HINT = 140;

/** Text clamped to its first 2 lines with "more" / "less". Rendered ONCE: the empty <details class="mr"> only flips the
 *  clamp through CSS `:has()` (globals.css `.cl`), so it works in server components without JS or a second copy. */
export function LongText({ text, className = "" }: { text: string; className?: string }) {
  const long = text.length > CLAMP_HINT || text.split("\n").length > 2;
  return (
    <div className={`cl ${className}`}>
      <div className="ct">{text}</div>
      {long && (
        <details className="mr">
          <summary className="sm" />
        </details>
      )}
    </div>
  );
}
