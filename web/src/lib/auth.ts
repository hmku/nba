import "server-only";
import { createHmac, timingSafeEqual } from "crypto";
import { cookies } from "next/headers";

export const SESSION_COOKIE = "fdash_session";

function token(passphrase: string): string {
  const salt = process.env.SUPABASE_SERVICE_ROLE_KEY || "fantasy-dashboard";
  return createHmac("sha256", salt).update(`v1:${passphrase}`).digest("hex");
}

export function expectedToken(): string | null {
  const p = process.env.DASHBOARD_PASSPHRASE;
  return p ? token(p) : null;
}

export function checkPassphrase(input: string): boolean {
  const p = process.env.DASHBOARD_PASSPHRASE;
  if (!p) return false;
  const a = Buffer.from(token(input));
  const b = Buffer.from(token(p));
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function isAuthed(): Promise<boolean> {
  const exp = expectedToken();
  if (!exp) return false;
  const jar = await cookies();
  const got = jar.get(SESSION_COOKIE)?.value;
  if (!got || got.length !== exp.length) return false;
  return timingSafeEqual(Buffer.from(got), Buffer.from(exp));
}
