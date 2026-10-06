import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const idempotencyKey = request.headers.get("idempotency-key") || "";
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}$/.test(idempotencyKey)) {
    return noStoreJson({ error: "A valid Idempotency-Key is required." }, { status: 400 });
  }
  try {
    const body = await readJsonBody(request);
    const query = body && typeof body === "object" ? String((body as Record<string, unknown>).query || "").trim() : "";
    const parentRunId = body && typeof body === "object" ? String((body as Record<string, unknown>).parent_run_id || "").trim() : "";
    if (query.length < 3 || query.length > 4000) {
      return noStoreJson({ error: "Research query must be 3-4000 characters." }, { status: 400 });
    }
    if (parentRunId && !/^agrun_[a-zA-Z0-9_-]{8,64}$/.test(parentRunId)) {
      return noStoreJson({ error: "Parent research run is invalid." }, { status: 400 });
    }
    const response = await authorizedBackendFetch("/v1/research-runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ query, parent_run_id: parentRunId }),
    });
    return forwardJson(response, "Research run could not be created.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Research run could not be created.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}

export async function GET(request: NextRequest) {
  const parsed = Number(request.nextUrl.searchParams.get("limit") || 50);
  const limit = Number.isFinite(parsed) ? Math.max(1, Math.min(Math.floor(parsed), 100)) : 50;
  try {
    const response = await authorizedBackendFetch(`/v1/research-runs?limit=${limit}`);
    return forwardJson(response, "Research threads are unavailable.");
  } catch {
    return noStoreJson({ error: "Research threads are unavailable." }, { status: 503 });
  }
}
