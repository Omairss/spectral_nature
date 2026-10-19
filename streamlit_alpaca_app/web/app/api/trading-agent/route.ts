import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";

export async function GET() {
  const response = await authorizedBackendFetch("/v1/trading-agent", { timeoutMs: 45_000 });
  return forwardJson(response, "Trading Agent data could not be loaded.");
}
