import { readFileSync } from "fs";
import path from "path";
import type { ReactNode } from "react";

/** Static instruction pages for external agents (/agent for Muse, /advisor for the advisor), rendered from
 *  web/src/content/*.md (mirrored to docs/integrations/*.md; tests keep them identical). */
export function loadContent(name: string): string {
  return readFileSync(path.join(process.cwd(), "src/content", name), "utf8");
}

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|https:\/\/[^\s)]+)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    let t = m[0];
    if (t.startsWith("https://")) {
      t = t.replace(/[.,;:]+$/, ""); // trailing sentence punctuation is not part of the URL
      re.lastIndex = m.index + t.length;
    }
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${i++}`;
    if (t.startsWith("`")) out.push(<code key={k} className="rounded bg-panel-2 px-1 py-0.5 text-[0.92em]">{t.slice(1, -1)}</code>);
    else if (t.startsWith("**")) out.push(<strong key={k}>{t.slice(2, -2)}</strong>);
    else
      out.push(
        <a key={k} href={t} className="text-accent underline-offset-2 hover:underline">
          {t}
        </a>,
      );
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Minimal Markdown → JSX (headings, paragraphs, bullet/numbered lists with continuation lines, code fences). */
function render(md: string): ReactNode[] {
  const lines = md.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let n = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${n++}`;
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(
        <pre key={key} className="overflow-x-auto rounded-lg border border-line bg-panel-2 p-3 text-[12px] leading-relaxed">
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) {
      const lvl = h[1].length;
      const cls = lvl === 1 ? "text-xl font-semibold tracking-tight" : lvl === 2 ? "mt-6 text-lg font-semibold" : "mt-4 text-base font-semibold";
      const Tag = (`h${lvl}` as "h1" | "h2" | "h3");
      blocks.push(<Tag key={key} className={cls}>{inline(h[2], key)}</Tag>);
      i++;
      continue;
    }
    const li = /^(\s*)(-|\d+\.)\s+(.*)$/;
    if (li.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: { depth: number; text: string }[] = [];
      while (i < lines.length && (li.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const m = li.exec(lines[i]);
        if (m) items.push({ depth: m[1].length >= 2 ? 1 : 0, text: m[3] });
        else items[items.length - 1].text += " " + lines[i].trim();
        i++;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={key} className={`space-y-1.5 pl-5 ${ordered ? "list-decimal" : "list-disc"}`}>
          {items.map((it, j) => (
            <li key={j} className={it.depth ? "ml-5 list-[circle]" : ""}>
              {inline(it.text, `${key}-${j}`)}
            </li>
          ))}
        </ListTag>,
      );
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|```|\s*(-|\d+\.)\s)/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={key}>{inline(para.join(" "), key)}</p>);
  }
  return blocks;
}

export function MarkdownDoc({ md }: { md: string }) {
  return <article className="mx-auto max-w-3xl space-y-3 text-[14.5px] leading-relaxed">{render(md)}</article>;
}
