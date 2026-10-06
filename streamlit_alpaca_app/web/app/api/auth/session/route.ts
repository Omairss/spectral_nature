import { authorizedBackendFetch } from "@/lib/server/backend";
import { clearAuthCookies } from "@/lib/server/auth";
import { noStoreJson } from "@/lib/server/http";

export async function GET() {
  try {
    const response = await authorizedBackendFetch("/v1/me", { timeoutMs: 15_000 });
    if (response.status === 401 || response.status === 403) {
      await clearAuthCookies();
      return noStoreJson({ authenticated: false });
    }
    if (!response.ok) return noStoreJson({ authenticated: false }, { status: 503 });
    return noStoreJson(await response.json());
  } catch {
    return noStoreJson({ authenticated: false }, { status: 503 });
  }
}
