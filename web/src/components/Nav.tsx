"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle, WriteAccess } from "./client";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/draft", label: "Draft" },
  { href: "/agents", label: "Agents" },
  { href: "/approvals", label: "Approvals" },
  { href: "/players", label: "Players" },
  { href: "/history", label: "History" },
];

export function Nav() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <>
      {/* desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-48 shrink-0 flex-col border-r border-line bg-panel md:flex">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="grid h-6 w-6 place-items-center rounded bg-accent text-[11px] font-bold text-accent-fg">FB</span>
          <span className="text-sm font-semibold tracking-tight">Fantasy Desk</span>
        </div>
        <nav className="flex flex-col gap-0.5 px-2">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`rounded px-2.5 py-1.5 text-sm ${
                active(l.href) ? "bg-panel-2 font-medium text-fg" : "text-muted hover:bg-panel-2 hover:text-fg"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto flex items-center justify-between border-t border-line px-3 py-3">
          <WriteAccess compact />
          <ThemeToggle />
        </div>
      </aside>
      {/* mobile top bar */}
      <div className="sticky top-0 z-40 border-b border-line bg-panel/95 backdrop-blur md:hidden">
        <div className="flex items-center justify-between px-3 pt-2">
          <span className="text-sm font-semibold tracking-tight">Fantasy Desk</span>
          <div className="flex items-center gap-3">
            <WriteAccess compact />
            <ThemeToggle />
          </div>
        </div>
        <nav className="scroll-thin flex gap-1 overflow-x-auto px-2 py-1.5">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`shrink-0 rounded px-2.5 py-1 text-sm ${
                active(l.href) ? "bg-panel-2 font-medium text-fg" : "text-muted"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </>
  );
}
