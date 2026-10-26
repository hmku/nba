"use client";

import { useEffect, useState } from "react";
import { useWriteStatus } from "./client";

/** VAPID public key (base64url) -> bytes for PushManager.subscribe. */
function keyBytes(b64url: string): Uint8Array<ArrayBuffer> {
  const b64 = (b64url + "=".repeat((4 - (b64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // ServiceWorkerRegister only registers in production; registering again is a no-op there and makes dev work too.
  await navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
  return navigator.serviceWorker.ready;
}

type State = "loading" | "unsupported" | "unconfigured" | "denied" | "off" | "on";

/**
 * "Notify me" control for new approvals (Web Push), shown only after the passphrase unlock. On iPhone push works only
 * in the Home Screen app (iOS 16.4+): in Safari itself PushManager is missing and this shows a hint instead.
 * Server side: /api/push (subscribe/unsubscribe), /api/push/test, /api/push/notify (lib/push.ts).
 */
export function PushToggle() {
  const ws = useWriteStatus();
  const [state, setState] = useState<State>("loading");
  const [publicKey, setPublicKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!ws.authed) return;
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setState("unsupported");
      return;
    }
    let live = true;
    (async () => {
      const cfg = await fetch("/api/push", { cache: "no-store" })
        .then((r) => r.json())
        .catch(() => ({}));
      if (!live) return;
      if (!cfg.configured || !cfg.publicKey) return setState("unconfigured");
      setPublicKey(cfg.publicKey);
      if (Notification.permission === "denied") return setState("denied");
      const reg = await navigator.serviceWorker.getRegistration("/");
      const sub = await reg?.pushManager.getSubscription();
      if (live) setState(sub ? "on" : "off");
    })().catch(() => live && setState("off"));
    return () => {
      live = false;
    };
  }, [ws.authed]);

  if (!ws.authed || state === "loading") return null;

  const run = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setMsg(null);
    try {
      setMsg(await fn());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const enable = () =>
    run(async () => {
      const perm = await Notification.requestPermission(); // first, inside the tap (iOS requires a user gesture)
      if (perm !== "granted") {
        setState(perm === "denied" ? "denied" : "off");
        return "Permission not granted.";
      }
      const reg = await registration();
      await (await reg.pushManager.getSubscription())?.unsubscribe(); // VAPID key may have changed
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey!) });
      const r = await fetch("/api/push", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ subscription: sub.toJSON() }),
      });
      if (!r.ok) {
        await sub.unsubscribe();
        return (await r.json().catch(() => ({}))).error || `HTTP ${r.status}`;
      }
      setState("on");
      return "On: you'll get a notification for each new approval.";
    });

  const disable = () =>
    run(async () => {
      const sub = await (await navigator.serviceWorker.getRegistration("/"))?.pushManager.getSubscription();
      if (sub) {
        await fetch("/api/push", {
          method: "DELETE",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        await sub.unsubscribe();
      }
      setState("off");
      return "Notifications off on this device.";
    });

  const test = () =>
    run(async () => {
      const sub = await (await navigator.serviceWorker.getRegistration("/"))?.pushManager.getSubscription();
      if (!sub) return "Not subscribed.";
      const r = await fetch("/api/push/test", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
      return r.ok ? "Test sent." : (await r.json().catch(() => ({}))).error || `HTTP ${r.status}`;
    });

  const btn = "rounded-md border border-line px-2 py-0.5 text-xs disabled:opacity-40";
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
      {state === "unsupported" && <span>Notifications: add to Home Screen, open from the icon.</span>}
      {state === "unconfigured" && <span>Notifications not configured.</span>}
      {state === "denied" && <span>Notifications blocked in settings.</span>}
      {state === "off" && (
        <button onClick={enable} disabled={busy} className={btn}>
          🔔 Enable notifications
        </button>
      )}
      {state === "on" && (
        <>
          <span>🔔 on</span>
          <button onClick={test} disabled={busy} className={btn}>
            Test
          </button>
          <button onClick={disable} disabled={busy} className={btn}>
            Turn off
          </button>
        </>
      )}
      {msg && <span role="status">{msg}</span>}
    </div>
  );
}
