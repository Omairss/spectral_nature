import { NextRequest } from "next/server";
import { validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, readJsonBody } from "@/lib/server/http";

export async function POST(request: NextRequest, context: {params: Promise<{action: string}>}) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  const {action} = await context.params;
  if (!["refresh", "review", "confirm"].includes(action)) return noStoreJson({error: "Unknown paper trading action."}, {status: 404});
  const body = await readJsonBody(request, 4096);
  if (body instanceof Response) return body;
  const response = await authorizedBackendFetch(`/v1/trading-agent/paper/${action}`, {method: "POST", body: JSON.stringify(body), timeoutMs: 90_000});
  return forwardJson(response, "Paper trading is unavailable.");
}
