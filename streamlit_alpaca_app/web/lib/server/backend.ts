import "server-only";

import { cookies } from "next/headers";
import { ACCESS_COOKIE, clearAuthCookies, REFRESH_COOKIE, setAuthCookies } from "@/lib/server/auth";

type BackendOptions = RequestInit & {
  timeoutMs?: number;
  next?: { revalidate?: number; tags?: string[] };
};

type RefreshPayload = {
  access_token?: string;
  refresh_token?: string;
  access_token_expires_at?: string;
  refresh_token_expires_at?: string;
};

function backendBaseUrl() {
  const raw = process.env.SPECTRAL_API_BASE_URL?.trim();
  if (!raw) throw new Error("Backend is not configured.");
  const url = new URL(raw);
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error("Backend URL must use HTTPS.");
  }
  return url.toString().replace(/\/$/, "");
}

export async function backendFetch(path: string, options: BackendOptions = {}) {
  const { timeoutMs = 45_000, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const url = `${backendBaseUrl()}${path}`;
  const deadline = AbortSignal.timeout(timeoutMs);
  try {
    return await fetch(url, {
      ...init,
      headers,
      cache: init.cache || "no-store",
      redirect: "error",
      signal: deadline,
    });
  } catch {
    return Response.json({ error: deadline.aborted ? "Service request timed out." : "Service is unavailable." }, {
      status: deadline.aborted ? 504 : 503,
    });
  }
}

export async function authorizedBackendFetch(path: string, options: BackendOptions = {}) {
  const store = await cookies();
  let accessToken = store.get(ACCESS_COOKIE)?.value || "";
  const requestHeaders = new Headers(options.headers);
  if (accessToken) requestHeaders.set("Authorization", `Bearer ${accessToken}`);
  let response = await backendFetch(path, { ...options, headers: requestHeaders });

  if (response.status !== 401) return response;
  const refreshToken = store.get(REFRESH_COOKIE)?.value || "";
  if (!refreshToken) return response;

  const refreshed = await backendFetch("/v1/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: refreshToken, rotate_refresh_token: true }),
  });
  if (!refreshed.ok) {
    if (refreshed.status === 401 || refreshed.status === 403) await clearAuthCookies();
    return refreshed;
  }
  const payload = (await refreshed.json()) as RefreshPayload;
  if (!payload.access_token || !payload.refresh_token) {
    return Response.json({ error: "Session could not be verified." }, { status: 502 });
  }
  await setAuthCookies({
    access_token: payload.access_token,
    refresh_token: payload.refresh_token,
    access_token_expires_at: payload.access_token_expires_at,
    refresh_token_expires_at: payload.refresh_token_expires_at,
  });
  accessToken = payload.access_token;
  requestHeaders.set("Authorization", `Bearer ${accessToken}`);
  response = await backendFetch(path, { ...options, headers: requestHeaders });
  return response;
}

export async function backendError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: unknown; error?: unknown };
    const value = typeof payload.detail === "string" ? payload.detail : payload.error;
    if (typeof value === "string" && value.trim()) return value.trim().slice(0, 500);
  } catch {
    // Return the stable surface error below.
  }
  return fallback;
}
