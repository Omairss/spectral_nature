import { NextRequest } from "next/server";
import { validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { readJsonBody, forwardJson, noStoreJson, validUuid } from "@/lib/server/http";

export async function POST(request: NextRequest, context: { params: Promise<{ requestId: string }> }) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const { requestId } = await context.params;
  if (!validUuid(requestId)) return noStoreJson({ error: "Feature request not found." }, { status: 404 });
  const body = await readJsonBody(request);
  if (body instanceof Response) return body;
  return forwardJson(await authorizedBackendFetch(`/v1/feature-requests/${encodeURIComponent(requestId)}/messages`, {
    method: "POST", body: JSON.stringify(body),
  }), "The message could not be sent.");
}
