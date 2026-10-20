"use client";

import Link, { useLinkStatus } from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { ThemeToggle, WriteAccess } from "./client";
import { SUPABASE_ANON_KEY, SUPABASE_URL } from "@/lib/env";

const TABS = [
  { href: "/week", label: "This week", icon: "⚔" },
  { href: "/league", label: "League", icon: "▦" },
  { href: "/inbox", label: "Inbox", icon: "\u2709\uFE0E" },
  { href: "/draft", label: "Draft", icon: "◎" },
];

/** Count of pending approvals via a bare PostgREST HEAD request (no supabase-js in the shared bundle). */
async function fetchPendingCount(): Promise<number | null> {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/approvals?select=approval_id&status=eq.pending`, {
    method: "HEAD",
    headers: { apikey: SUPABASE_ANON_KEY, Prefer: "count=exact" },
    cache: "no-store",
  });
  if (!r.ok) return null;
  const total = r.headers.get("content-range")?.split("/")[1]; // "0-0/3" or "*/0"
  const n = total != null ? Number(total) : NaN;
  return Number.isFinite(n) ? n : null;
}

/** Pending-approval count for the Inbox badge (polls every 30s while visible). */
function usePendingApprovals(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      fetchPendingCount()
        .then((c) => {
          if (alive && c != null) setN(c);
        })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 30000);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(t);
      document.removeEventListener("visibilitychange", load);
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
  if (href === "/week") return path.startsWith("/week") || path.startsWith("/matchup") || path.startsWith("/lineup");
  if (href === "/inbox")
    return ["/inbox", "/moves", "/activity", "/agents", "/approvals"].some((p) => path.startsWith(p));
  return path.startsWith(href);
}

/**
 * The path to highlight: the tapped tab immediately (optimistic), until the router commits the new
 * pathname (with loading.tsx that is almost instant) — or after 10 s, if the navigation never lands.
 */
function useOptimisticPath() {
  const path = usePathname();
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => setPending(null), [path]);
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(() => setPending(null), 10000);
    return () => clearTimeout(t);
  }, [pending]);
  const onNavigate = (href: string) => (e: MouseEvent) => {
    // new-tab / new-window clicks don't navigate this page
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (!isActive(path, href)) setPending(href);
  };
  return { path, shown: pending ?? path, onNavigate };
}

/** Pulses the tab label while its navigation is in flight (only shows if the route wasn't prefetched yet). */
function PendingPulse({ children }: { children: ReactNode }) {
  const { pending } = useLinkStatus();
  return <span className={pending ? "motion-safe:animate-pulse" : undefined}>{children}</span>;
}

function MoreMenu({ up = false }: { up?: boolean }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const active = MORE.some((m) => path.startsWith(m.href));
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    if (!open) return;
    const h = (e: globalThis.MouseEvent) => {
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
            ? `relative flex w-full flex-col items-center gap-0.5 pt-1.5 pb-2 text-[11px] ${active ? `font-semibold text-tab-fg ${BAR_TOP}` : "text-muted"}`
            : `relative rounded-md px-3 py-1.5 text-sm ${active ? `bg-tab-bg font-semibold text-tab-fg ${BAR_BOTTOM}` : "text-muted hover:bg-panel-2 hover:text-fg"}`
        }
      >
        {up && <span className={`rounded-full px-3 py-0.5 text-lg leading-none ${active ? "bg-tab-bg" : ""}`}>⋯</span>}
        More
      </button>
      {open && (
        <div
          className={`absolute z-50 w-44 rounded-lg border border-line bg-panel p-1 shadow-xl ${
            up ? "right-1 bottom-full mb-2" : "right-0 mt-2"
          }`}
        >
          {MORE.map((m) => {
            const on = path.startsWith(m.href);
            return (
              <Link
                key={m.href}
                href={m.href}
                aria-current={on ? "page" : undefined}
                className={`block rounded px-3 py-2.5 text-sm ${on ? "bg-tab-bg font-semibold text-tab-fg" : "hover:bg-panel-2"}`}
              >
                <PendingPulse>{m.label}</PendingPulse>
              </Link>
            );
          })}
          <div className="mt-1 flex items-center justify-between border-t border-line px-3 pt-2 pb-1 text-xs text-muted">
            Theme <ThemeToggle />
          </div>
        </div>
      )}
    </div>
  );
}

// Selected-tab indicator bars (accent, ≥5:1 against the bar background in both themes): under the desktop
// tab (sits on the header's bottom border) and along the top edge of the phone tab.
const BAR_BOTTOM = "after:absolute after:inset-x-2 after:-bottom-2 after:h-0.5 after:rounded-full after:bg-accent";
const BAR_TOP = "before:absolute before:inset-x-3 before:top-0 before:h-0.5 before:rounded-full before:bg-accent";

export function Nav() {
  const { path, shown, onNavigate } = useOptimisticPath();
  const pending = usePendingApprovals();
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-line bg-bg/90 pt-[env(safe-area-inset-top)] backdrop-blur">
        <div className="mx-auto flex h-12 max-w-5xl items-center gap-4 pr-[max(1rem,env(safe-area-inset-right))] pl-[max(1rem,env(safe-area-inset-left))]">
          <Link href="/" className="text-[15px] font-semibold tracking-tight">
            Fantasy Desk
          </Link>
          <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
            {TABS.map((t) => {
              const on = isActive(shown, t.href);
              return (
                <Link
                  key={t.href}
                  href={t.href}
                  onClick={onNavigate(t.href)}
                  aria-current={isActive(path, t.href) ? "page" : undefined}
                  className={`relative rounded-md px-3 py-1.5 text-sm ${
                    on ? `bg-tab-bg font-semibold text-tab-fg ${BAR_BOTTOM}` : "text-muted hover:bg-panel-2 hover:text-fg"
                  }`}
                >
                  <PendingPulse>{t.label}</PendingPulse>
                  {t.href === "/inbox" && <Badge n={pending} />}
                </Link>
              );
            })}
            <MoreMenu />
          </nav>
          <div className="ml-auto">
            <WriteAccess compact />
          </div>
        </div>
      </header>
      {/* phone: bottom tab bar */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 px-[env(safe-area-inset-left)] pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        <div className="grid grid-cols-5">
          {TABS.map((t) => {
            const on = isActive(shown, t.href);
            return (
              <Link
                key={t.href}
                href={t.href}
                onClick={onNavigate(t.href)}
                aria-current={isActive(path, t.href) ? "page" : undefined}
                className={`relative flex flex-col items-center gap-0.5 pt-1.5 pb-2 text-[11px] whitespace-nowrap ${on ? `font-semibold text-tab-fg ${BAR_TOP}` : "text-muted"}`}
              >
                <span className={`relative rounded-full px-3 py-0.5 text-lg leading-none ${on ? "bg-tab-bg" : ""}`}>
                  {t.icon}
                  {t.href === "/inbox" && pending > 0 && (
                    <span className="absolute -top-1 right-0">
                      <Badge n={pending} />
                    </span>
                  )}
                </span>
                <PendingPulse>{t.label}</PendingPulse>
              </Link>
            );
          })}
          <MoreMenu up />
        </div>
      </nav>
    </>
  );
}
