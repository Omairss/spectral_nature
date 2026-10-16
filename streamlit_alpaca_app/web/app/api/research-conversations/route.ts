import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const value = Number(request.nextUrl.searchParams.get("limit") || 50);
  const limit = Number.isFinite(value) ? Math.max(1, Math.min(Math.floor(value), 100)) : 50;
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/research-conversations?limit=${limit}`), "Conversations are unavailable.");
  } catch {
    return noStoreJson({ error: "Conversations are unavailable." }, { status: 503 });
  }
}
