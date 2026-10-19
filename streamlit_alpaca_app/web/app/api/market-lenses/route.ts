import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";

export async function GET() {
  const response = await authorizedBackendFetch("/v1/market-lenses", { timeoutMs: 30_000 });
  return forwardJson(response, "Market lenses could not be loaded.");
}
