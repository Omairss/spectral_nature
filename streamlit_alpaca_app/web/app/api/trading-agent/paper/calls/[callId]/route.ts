import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";
export async function GET(
  _request: Request,
  context: { params: Promise<{ callId: string }> },
) {
  const { callId } = await context.params;
  const response = await authorizedBackendFetch(
    `/v1/trading-agent/paper/calls/${encodeURIComponent(callId)}`,
    { timeoutMs: 45_000 },
  );
  return forwardJson(response, "Call history is unavailable.");
}
