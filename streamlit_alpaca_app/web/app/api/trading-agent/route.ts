import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";

export async function GET(request: Request) {
  const response = await authorizedBackendFetch(
    `/v1/trading-agent${new URL(request.url).search}`,
    { timeoutMs: 45_000 },
  );
  return forwardJson(response, "Trading Agent data could not be loaded.");
}
