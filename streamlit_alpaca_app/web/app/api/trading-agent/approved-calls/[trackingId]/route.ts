import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson, validUuid } from "@/lib/server/http";

export async function GET(_: NextRequest, context: { params: Promise<{ trackingId: string }> }) {
  const { trackingId } = await context.params;
  if (!validUuid(trackingId)) return noStoreJson({ error: "Tracked call not found." }, { status: 404 });
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/trading-agent/calls/${encodeURIComponent(trackingId)}`), "Call tracking is unavailable.");
  } catch {
    return noStoreJson({ error: "Call tracking is unavailable." }, { status: 503 });
  }
}
