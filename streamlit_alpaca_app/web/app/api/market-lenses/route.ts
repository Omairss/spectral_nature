import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET() {
  try {
    const response = await authorizedBackendFetch("/v1/market-lenses", { timeoutMs: 30_000 });
    return forwardJson(response, "Market lenses could not be loaded.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Market lenses could not be loaded.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}
