import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { validateMutation } from "@/lib/server/auth";
import { forwardJson, noStoreJson, validRunId } from "@/lib/server/http";

export async function POST(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const { runId } = await context.params;
  if (!validRunId(runId)) return noStoreJson({ error: "Research run not found." }, { status: 404 });
  try {
    const response = await authorizedBackendFetch(`/v1/research-runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    return forwardJson(response, "Research run could not be cancelled.");
  } catch {
    return noStoreJson({ error: "Research run could not be cancelled." }, { status: 503 });
  }
}
