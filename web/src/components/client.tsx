"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { ageSeconds, humanAge } from "@/lib/format";

export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function Ago({ ts, staleAfter, className = "" }: { ts: string | null | undefined; staleAfter?: number; className?: string }) {
  const now = useNow(5000);
  const s = ageSeconds(ts, now);
  const stale = staleAfter != null && (s == null || s > staleAfter);
  return (
    <span
      suppressHydrationWarning
      title={ts ?? undefined}
      className={`num ${stale ? "text-warn" : ""} ${className}`}
    >
      {humanAge(s)}
    </span>
  );
}

export function AutoRefresh({ seconds = 15 }: { seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, seconds * 1000);
    // re-render server data right after unlock/lock (private sections depend on the session cookie)
    const onAuth = () => router.refresh();
    window.addEventListener("fdash-auth", onAuth);
    return () => {
      clearInterval(t);
      window.removeEventListener("fdash-auth", onAuth);
    };
  }, [router, seconds]);
  return null;
}

type ThemeMode = "auto" | "light" | "dark";

function readMode(): ThemeMode {
  try {
    const t = localStorage.getItem("theme");
    return t === "light" || t === "dark" ? t : "auto";
  } catch {
    return "auto";
  }
}

/** Apply a theme mode: auto = light 07:00–19:00 in the viewer's local time, dark otherwise. */
function applyTheme(mode: ThemeMode = readMode()) {
  const h = new Date().getHours();
  const dark = mode === "dark" || (mode === "auto" && (h < 7 || h >= 19));
  document.documentElement.classList.toggle("dark", dark);
}

/** Re-evaluates the auto theme every 10 minutes and when the tab becomes visible. */
export function ThemeAuto() {
  useEffect(() => {
    const tick = () => applyTheme();
    const t = setInterval(tick, 10 * 60 * 1000);
    const vis = () => document.visibilityState === "visible" && tick();
    document.addEventListener("visibilitychange", vis);
    window.addEventListener("fdash-theme", tick);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", vis);
      window.removeEventListener("fdash-theme", tick);
    };
  }, []);
  return null;
}

const NEXT_MODE: Record<ThemeMode, ThemeMode> = { auto: "light", light: "dark", dark: "auto" };
const MODE_LABEL: Record<ThemeMode, string> = { auto: "◐ auto", light: "☀ light", dark: "☾ dark" };

/** Cycles auto → light → dark (persisted in localStorage). */
export function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>("auto");
  useEffect(() => setMode(readMode()), []);
  const toggle = () => {
    const next = NEXT_MODE[mode];
    try {
      if (next === "auto") localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
    } catch {
      /* ignore */
    }
    setMode(next);
    applyTheme(next);
    window.dispatchEvent(new Event("fdash-theme"));
  };
  return (
    <button
      onClick={toggle}
      className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-fg"
      aria-label={`Theme: ${mode} (tap to change)`}
      title="Theme: auto (light 7am–7pm) → light → dark"
    >
      {MODE_LABEL[mode]}
    </button>
  );
}

interface WriteStatus {
  loaded: boolean;
  writesEnabled: boolean;
  authed: boolean;
  reason: string | null;
}

export function useWriteStatus(): WriteStatus & { refresh: () => void } {
  const [st, setSt] = useState<WriteStatus>({ loaded: false, writesEnabled: false, authed: false, reason: null });
  const refresh = useCallback(() => {
    fetch("/api/auth", { cache: "no-store" })
      .then((r) => r.json())
      .then((j) => setSt({ loaded: true, writesEnabled: !!j.writesEnabled, authed: !!j.authed, reason: j.reason ?? null }))
      .catch(() => setSt({ loaded: true, writesEnabled: false, authed: false, reason: "Could not reach /api/auth" }));
  }, []);
  useEffect(() => {
    refresh();
    const h = () => refresh();
    window.addEventListener("fdash-auth", h);
    return () => window.removeEventListener("fdash-auth", h);
  }, [refresh]);
  return { ...st, refresh };
}

/** Compact lock/unlock control for writes. */
export function WriteAccess({ compact = false }: { compact?: boolean }) {
  const st = useWriteStatus();
  const [open, setOpen] = useState(false);
  const [pass, setPass] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const login = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const r = await fetch("/api/auth", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ passphrase: pass }),
    });
    setBusy(false);
    if (r.ok) {
      setPass("");
      setOpen(false);
      window.dispatchEvent(new Event("fdash-auth"));
    } else {
      const j = await r.json().catch(() => ({}));
      setErr(j.error || `HTTP ${r.status}`);
    }
  };
  const logout = async () => {
    await fetch("/api/auth", { method: "DELETE" });
    window.dispatchEvent(new Event("fdash-auth"));
  };

  if (!st.loaded) return <span className="text-xs text-muted">…</span>;
  if (!st.writesEnabled)
    return (
      <span className="text-xs text-muted" title={st.reason ?? ""}>
        {compact ? "read-only" : "Writes disabled (read-only)"}
      </span>
    );
  if (st.authed)
    return (
      <button onClick={logout} className="text-xs text-good hover:underline" title="Lock writes">
        ● unlocked
      </button>
    );
  return (
    <div className="relative">
      <button onClick={() => setOpen((o) => !o)} className="text-xs text-muted hover:text-fg">
        🔒 unlock
      </button>
      {open && (
        <form
          onSubmit={login}
          className="absolute right-0 z-50 mt-2 w-64 rounded-lg border border-line bg-panel p-3 shadow-xl"
        >
          <label className="mb-1 block text-xs text-muted">Dashboard passphrase</label>
          <input
            type="password"
            aria-label="Dashboard passphrase"
            autoFocus
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            className="w-full rounded border border-line bg-panel-2 px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          {err && <div className="mt-1 text-xs text-bad">{err}</div>}
          <button
            disabled={busy || !pass}
            className="mt-2 w-full rounded bg-accent px-2 py-1.5 text-sm font-medium text-accent-fg disabled:opacity-50"
          >
            {busy ? "Checking…" : "Unlock writes"}
          </button>
        </form>
      )}
    </div>
  );
}
