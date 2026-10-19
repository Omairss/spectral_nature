import "server-only";

import { randomBytes, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

const secureCookies = process.env.APP_COOKIE_SECURE !== "false";
const prefix = secureCookies ? "__Host-" : "";

export const ACCESS_COOKIE = `${prefix}sn_access`;
export const REFRESH_COOKIE = `${prefix}sn_refresh`;
export const CSRF_COOKIE = `${prefix}sn_csrf`;

export function safeReturnPath(raw: unknown, fallback = "/zopedia") {
  if (typeof raw !== "string" || raw.length > 2048 || !raw.startsWith("/")) return fallback;
  try {
    const base = new URL("https://return-path.invalid");
    const target = new URL(raw, base);
    return target.origin === base.origin ? `${target.pathname}${target.search}${target.hash}` : fallback;
  } catch {
    return fallback;
  }
}

type TokenPayload = {
  access_token: string;
  refresh_token: string;
  access_token_expires_at?: string;
  refresh_token_expires_at?: string;
};

function maxAge(expiresAt: string | undefined, fallbackSeconds: number) {
  if (!expiresAt) return fallbackSeconds;
  const seconds = Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000);
  return Number.isFinite(seconds) ? Math.max(seconds, 0) : fallbackSeconds;
}

function cookieOptions(seconds: number, httpOnly = true) {
  return {
    httpOnly,
    secure: secureCookies,
    sameSite: "lax" as const,
    path: "/",
    maxAge: seconds,
    priority: "high" as const,
  };
}

export async function setAuthCookies(tokens: TokenPayload) {
  const store = await cookies();
  store.set(ACCESS_COOKIE, tokens.access_token, cookieOptions(maxAge(tokens.access_token_expires_at, 900)));
  store.set(REFRESH_COOKIE, tokens.refresh_token, cookieOptions(maxAge(tokens.refresh_token_expires_at, 2_592_000)));
}

export async function clearAuthCookies() {
  const store = await cookies();
  for (const name of new Set([
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    CSRF_COOKIE,
    "sn_access",
    "sn_refresh",
    "sn_csrf",
    "__Host-sn_access",
    "__Host-sn_refresh",
    "__Host-sn_csrf",
  ])) {
    store.set(name, "", cookieOptions(0));
  }
}

export async function issueCsrfToken() {
  const store = await cookies();
  const existing = store.get(CSRF_COOKIE)?.value;
  if (existing && existing.length >= 32) return existing;
  const token = randomBytes(32).toString("base64url");
  store.set(CSRF_COOKIE, token, cookieOptions(21_600));
  return token;
}

function safeEqual(left: string, right: string) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

export async function validateMutation(request: NextRequest): Promise<NextResponse | null> {
  const configuredOrigins = (process.env.APP_PUBLIC_ORIGINS || process.env.APP_PUBLIC_ORIGIN || "")
    .split(",").map((value) => value.trim()).filter(Boolean);
  const allowedOrigins = configuredOrigins.length ? configuredOrigins : [request.nextUrl.origin];
  const origin = request.headers.get("origin") || "";
  // Multiple production hostnames are allowed, but one alias cannot mutate a
  // session on another. Host-prefixed cookies and CSRF remain origin-local.
  const requestHost = request.headers.get("host") || request.nextUrl.host;
  let originMatchesHost = false;
  try { originMatchesHost = new URL(origin).host === requestHost; } catch { /* Reject malformed origins. */ }
  if (!origin || !allowedOrigins.includes(origin) || !originMatchesHost) {
    return NextResponse.json({ error: "Request origin rejected." }, { status: 403 });
  }
  const store = await cookies();
  const cookieToken = store.get(CSRF_COOKIE)?.value || "";
  const headerToken = request.headers.get("x-csrf-token") || "";
  if (!cookieToken || !headerToken || !safeEqual(cookieToken, headerToken)) {
    return NextResponse.json({ error: "Request verification failed." }, { status: 403 });
  }
  return null;
}
