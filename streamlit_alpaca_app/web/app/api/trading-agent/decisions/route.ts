import { NextRequest } from "next/server";
import { validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { readJsonBody, forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  const body = await readJsonBody(request, 4096) as { candidate_id?: unknown; action?: unknown };
  if (body instanceof Response) return body;
  const candidateId = typeof body?.candidate_id === "string" ? body.candidate_id.trim() : "";
  const action = typeof body?.action === "string" ? body.action.trim().toLowerCase() : "";
  if (!candidateId || !["approve", "reject"].includes(action)) return noStoreJson({ error: "Decision is invalid." }, { status: 400 });
  const response = await authorizedBackendFetch("/v1/trading-agent/decisions", {
    method: "POST",
    body: JSON.stringify({ candidate_id: candidateId, action }),
    timeoutMs: 30_000,
  });
  return forwardJson(response, "Decision could not be recorded.");
}
