import { NextRequest } from "next/server";
import { clearAuthCookies, setAuthCookies, validateMutation } from "@/lib/server/auth";
import { backendError, backendFetch } from "@/lib/server/backend";
import { readJsonBody, noStoreJson } from "@/lib/server/http";

const actions = {
  invite: { path: "/v1/auth/invites/accept", fields: ["invite_token", "first_name", "last_name", "display_name", "password"] },
  recover: { path: "/v1/auth/password-reset/request", fields: ["email"] },
  reset: { path: "/v1/auth/password-reset/complete", fields: ["reset_token", "new_password"] },
} as const;

export async function POST(request: NextRequest, context: { params: Promise<{ action: string }> }) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const { action } = await context.params;
  if (!Object.hasOwn(actions, action)) return noStoreJson({ error: "Account action is invalid." }, { status: 400 });
  const definition = actions[action as keyof typeof actions];
  try {
    const raw = await readJsonBody(request);
    if (raw instanceof Response) return raw;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return noStoreJson({ error: "Request is invalid." }, { status: 400 });
    const input = raw as Record<string, unknown>;
    const body = Object.fromEntries(definition.fields.map((key) => [key, input[key] ?? ""]));
    const response = await backendFetch(definition.path, { method: "POST", body: JSON.stringify(body), timeoutMs: 30_000 });
    if (!response.ok) {
      // Pydantic validation details can echo credential input. Only forward the
      // service's safe string message, never raw error/diagnostic objects.
      return noStoreJson({ error: await backendError(response, "Account request is invalid.") }, { status: response.status });
    }
    if (action === "invite") {
      const tokens = await response.json();
      if (typeof tokens.access_token !== "string" || typeof tokens.refresh_token !== "string") {
        return noStoreJson({ error: "Account created. Please sign in." }, { status: 502 });
      }
      await setAuthCookies(tokens);
    } else if (action === "reset") {
      await clearAuthCookies();
    }
    return noStoreJson({ ok: true });
  } catch {
    return noStoreJson({ error: "Account service is unavailable." }, { status: 503 });
  }
}
