import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest, context: { params: Promise<{ ticker: string }> }) {
  const { ticker: rawTicker } = await context.params;
  const ticker = String(rawTicker || "").trim().toUpperCase();
  if (!/^[A-Z0-9.-]{1,12}$/.test(ticker)) {
    return noStoreJson({ error: "Ticker is invalid." }, { status: 400 });
  }
  const rawDays = Number(request.nextUrl.searchParams.get("days") || 365);
  const days = Number.isFinite(rawDays) ? Math.min(720, Math.max(60, Math.round(rawDays))) : 365;
  const response = await authorizedBackendFetch(
    `/v1/investigator/${encodeURIComponent(ticker)}?days=${days}`,
    { timeoutMs: 45_000 },
  );
  return forwardJson(response, "Stock Investigator data could not be loaded.");
}
