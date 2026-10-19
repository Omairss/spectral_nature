import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { validateMutation } from "@/lib/server/auth";
import { readJsonBody, forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const idempotencyKey = request.headers.get("idempotency-key") || "";
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}$/.test(idempotencyKey)) {
    return noStoreJson({ error: "A valid Idempotency-Key is required." }, { status: 400 });
  }
  const body = await readJsonBody(request);
  if (body instanceof Response) return body;
  const query = body && typeof body === "object" ? String((body as Record<string, unknown>).query || "").trim() : "";
  const parentRunId = body && typeof body === "object" ? String((body as Record<string, unknown>).parent_run_id || "").trim() : "";
  const fields = body && typeof body === "object" ? body as Record<string, unknown> : {};
  const conversationId = String(fields.conversation_id || "");
  if (query.length < 1 || query.length > 4000) {
    return noStoreJson({ error: "Research query must be 1-4000 characters." }, { status: 400 });
  }
  if (parentRunId && !/^agrun_[a-zA-Z0-9_-]{8,64}$/.test(parentRunId)) {
    return noStoreJson({ error: "Parent research run is invalid." }, { status: 400 });
  }
  if (conversationId && !/^zthread_[a-zA-Z0-9_-]{8,64}$/.test(conversationId)) {
    return noStoreJson({ error: "Research conversation is invalid." }, { status: 400 });
  }
  const response = await authorizedBackendFetch("/v1/research-runs", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ query, parent_run_id: parentRunId, conversation_id: conversationId,
      timezone: fields.timezone, asof_time_utc: fields.asof_time_utc,
      allow_web: fields.allow_web, visible_artifacts: fields.visible_artifacts }),
  });
  return forwardJson(response, "Research run could not be created.");
}

export async function GET(request: NextRequest) {
  const parsed = Number(request.nextUrl.searchParams.get("limit") || 50);
  const limit = Number.isFinite(parsed) ? Math.max(1, Math.min(Math.floor(parsed), 100)) : 50;
  const response = await authorizedBackendFetch(`/v1/research-runs?limit=${limit}`);
  return forwardJson(response, "Research threads are unavailable.");
}
