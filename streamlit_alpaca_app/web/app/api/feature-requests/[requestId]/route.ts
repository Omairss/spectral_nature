import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, validUuid } from "@/lib/server/http";

export async function GET(_: NextRequest, context: { params: Promise<{ requestId: string }> }) {
  const { requestId } = await context.params;
  if (!validUuid(requestId)) return noStoreJson({ error: "Feature request not found." }, { status: 404 });
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/feature-requests/${encodeURIComponent(requestId)}`), "Feature request unavailable.");
  } catch {
    return noStoreJson({ error: "Feature request unavailable." }, { status: 503 });
  }
}
