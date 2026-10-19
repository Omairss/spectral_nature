import { NextRequest } from "next/server";
import { backendError, backendFetch } from "@/lib/server/backend";
import { setAuthCookies, validateMutation } from "@/lib/server/auth";
import { readJsonBody, noStoreJson } from "@/lib/server/http";

type LoginPayload = {
  access_token?: string;
  refresh_token?: string;
  access_token_expires_at?: string;
  refresh_token_expires_at?: string;
  context?: unknown;
};

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  try {
    const body = await readJsonBody(request);
    if (body instanceof Response) return body;
    if (!body || typeof body !== "object") return noStoreJson({ error: "Email and password are required." }, { status: 400 });
    const email = String((body as Record<string, unknown>).email || "").trim().toLowerCase();
    const password = String((body as Record<string, unknown>).password || "");
    if (!email || !password || email.length > 320 || password.length > 1024) {
      return noStoreJson({ error: "Email and password are required." }, { status: 400 });
    }
    const response = await backendFetch("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      timeoutMs: 20_000,
    });
    if (!response.ok) {
      return noStoreJson({ error: await backendError(response, "Sign in failed.") }, { status: response.status });
    }
    const payload = (await response.json()) as LoginPayload;
    if (!payload.access_token || !payload.refresh_token) {
      return noStoreJson({ error: "Sign in failed." }, { status: 502 });
    }
    await setAuthCookies({
      access_token: payload.access_token,
      refresh_token: payload.refresh_token,
      access_token_expires_at: payload.access_token_expires_at,
      refresh_token_expires_at: payload.refresh_token_expires_at,
    });
    return noStoreJson({ authenticated: true, context: payload.context || null });
  } catch {
    return noStoreJson({ error: "Sign in failed." }, { status: 502 });
  }
}
