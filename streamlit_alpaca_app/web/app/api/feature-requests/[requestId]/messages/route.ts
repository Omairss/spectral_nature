import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, validUuid } from "@/lib/server/http";

export async function POST(request: NextRequest, context: { params: Promise<{ requestId: string }> }) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const { requestId } = await context.params;
  if (!validUuid(requestId)) return noStoreJson({ error: "Feature request not found." }, { status: 404 });
  let body: unknown;
  try { body = await readJsonBody(request); }
  catch { return noStoreJson({ error: "The message could not be read." }, { status: 400 }); }
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/feature-requests/${encodeURIComponent(requestId)}/messages`, {
      method: "POST", body: JSON.stringify(body),
    }), "The message could not be sent.");
  } catch {
    return noStoreJson({ error: "The message could not be sent. Please retry." }, { status: 503 });
  }
}
