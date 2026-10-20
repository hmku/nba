"use client";

import { useEffect } from "react";

/** Registers /sw.js (production only) — enables "Install app" and the offline fallback page. See public/sw.js. */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {
      /* non-fatal: the dashboard works without it */
    });
  }, []);
  return null;
}
