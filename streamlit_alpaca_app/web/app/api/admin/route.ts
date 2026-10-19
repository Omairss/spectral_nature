import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const raw = Number(request.nextUrl.searchParams.get("days") || 7);
  const days = Number.isFinite(raw) ? Math.min(30, Math.max(1, Math.round(raw))) : 7;
  const response = await authorizedBackendFetch(`/v1/admin?days=${days}`, { timeoutMs: 60_000 });
  return forwardJson(response, "Admin data could not be loaded.");
}
