import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const raw = Number(request.nextUrl.searchParams.get("hours") || 24);
  const hours = Number.isFinite(raw) ? Math.min(168, Math.max(1, Math.round(raw))) : 24;
  const resource = request.nextUrl.searchParams.get("resource") || "";
  try {
    const query = new URLSearchParams({ hours: String(hours), resource });
    const response = await authorizedBackendFetch(`/v1/admin/health?${query}`, { timeoutMs: 45_000 });
    return forwardJson(response, "Health data is unavailable.");
  } catch {
    return noStoreJson({ error: "Health data is unavailable." }, { status: 503 });
  }
}
