import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { clearAuthCookies, REFRESH_COOKIE, validateMutation } from "@/lib/server/auth";
import { noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const store = await cookies();
  const refreshToken = store.get(REFRESH_COOKIE)?.value || "";
  try {
    await authorizedBackendFetch("/v1/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refreshToken }),
      timeoutMs: 15_000,
    });
  } catch {
    // Local cookie revocation still closes this browser session.
  }
  await clearAuthCookies();
  return noStoreJson({ ok: true });
}
