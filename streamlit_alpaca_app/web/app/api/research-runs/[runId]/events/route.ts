import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, validRunId } from "@/lib/server/http";

export async function GET(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  if (!validRunId(runId)) return noStoreJson({ error: "Research run not found." }, { status: 404 });
  const parsed = Number(request.nextUrl.searchParams.get("after_sequence") || 0);
  const after = Number.isFinite(parsed) ? Math.max(Math.min(Math.floor(parsed), 1_000_000_000), 0) : 0;
  try {
    const response = await authorizedBackendFetch(
      `/v1/research-runs/${encodeURIComponent(runId)}/events?after_sequence=${after}`,
    );
    return forwardJson(response, "Research events are unavailable.");
  } catch {
    return noStoreJson({ error: "Research events are unavailable." }, { status: 503 });
  }
}
