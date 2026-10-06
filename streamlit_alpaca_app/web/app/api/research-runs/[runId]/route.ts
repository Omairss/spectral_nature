import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, validRunId } from "@/lib/server/http";

export async function GET(_: NextRequest, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  if (!validRunId(runId)) return noStoreJson({ error: "Research run not found." }, { status: 404 });
  try {
    const response = await authorizedBackendFetch(`/v1/research-runs/${encodeURIComponent(runId)}`);
    return forwardJson(response, "Research run is unavailable.");
  } catch {
    return noStoreJson({ error: "Research run is unavailable." }, { status: 503 });
  }
}
