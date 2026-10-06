import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET() {
  try {
    const response = await authorizedBackendFetch("/v1/trading-agent", { timeoutMs: 45_000 });
    return forwardJson(response, "Trading Agent data could not be loaded.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Trading Agent data could not be loaded.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}
