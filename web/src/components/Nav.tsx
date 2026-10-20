"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ThemeToggle, WriteAccess } from "./client";
import { anonClient } from "@/lib/supabase";

const TABS = [
  { href: "/matchup", label: "Matchup", icon: "⚔" },
  { href: "/league", label: "League", icon: "▦" },
  { href: "/activity", label: "Activity", icon: "≋" },
  { href: "/draft", label: "Draft", icon: "◎" },
];

/** Pending-approval count for the Activity badge (polls every 30s while visible). */
function usePendingApprovals(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      anonClient()
        .from("approvals")
        .select("approval_id", { count: "exact", head: true })
        .eq("status", "pending")
        .then(({ count, error }) => {
          if (alive && !error) setN(count ?? 0);
        });
    };
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  return n;
}

function Badge({ n }: { n: number }) {
  if (!n) return null;
  return (
    <span className="num ml-1 inline-grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] leading-none font-semibold text-accent-fg">
      {n}
    </span>
  );
}

const MORE = [
  { href: "/players", label: "Players" },
  { href: "/history", label: "History" },
];

function isActive(path: string, href: string) {
  if (href === "/activity") return path.startsWith("/activity") || path.startsWith("/agents") || path.startsWith("/approvals");
  return path.startsWith(href);
}

function MoreMenu({ up = false }: { up?: boolean }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const active = MORE.some((m) => path.startsWith(m.href));
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", h);
    return () => document.removeEventListener("click", h);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={
          up
            ? `flex w-full flex-col items-center gap-0.5 py-2 text-[11px] ${active ? "text-fg" : "text-muted"}`
            : `rounded-md px-3 py-1.5 text-sm ${active ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"}`
        }
      >
        {up && <span className="text-lg leading-none">⋯</span>}
        More
      </button>
      {open && (
        <div
          className={`absolute z-50 w-44 rounded-lg border border-line bg-panel p-1 shadow-xl ${
            up ? "right-1 bottom-full mb-2" : "right-0 mt-2"
          }`}
        >
          {MORE.map((m) => (
            <Link key={m.href} href={m.href} className="block rounded px-3 py-2.5 text-sm hover:bg-panel-2">
              {m.label}
            </Link>
          ))}
          <div className="mt-1 flex items-center justify-between border-t border-line px-3 pt-2 pb-1 text-xs text-muted">
            Theme <ThemeToggle />
          </div>
        </div>
      )}
    </div>
  );
}

export function Nav() {
  const path = usePathname();
  const pending = usePendingApprovals();
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-5xl items-center gap-4 px-4">
          <Link href="/" className="text-[15px] font-semibold tracking-tight">
            Fantasy Desk
          </Link>
          <nav className="hidden items-center gap-1 md:flex">
            {TABS.map((t) => (
              <Link
                key={t.href}
                href={t.href}
                className={`rounded-md px-3 py-1.5 text-sm ${isActive(path, t.href) ? "bg-panel-2 text-fg" : "text-muted hover:text-fg"}`}
              >
                {t.label}
                {t.href === "/activity" && <Badge n={pending} />}
              </Link>
            ))}
            <MoreMenu />
          </nav>
          <div className="ml-auto">
            <WriteAccess compact />
          </div>
        </div>
      </header>
      {/* phone: bottom tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <div className="grid grid-cols-5">
          {TABS.map((t) => (
            <Link
              key={t.href}
              href={t.href}
              className={`flex flex-col items-center gap-0.5 py-2 text-[11px] ${isActive(path, t.href) ? "font-medium text-accent" : "text-muted"}`}
            >
              <span className="relative text-lg leading-none">
                {t.icon}
                {t.href === "/activity" && pending > 0 && (
                  <span className="absolute -top-1 -right-3">
                    <Badge n={pending} />
                  </span>
                )}
              </span>
              {t.label}
            </Link>
          ))}
          <MoreMenu up />
        </div>
      </nav>
    </>
  );
}
