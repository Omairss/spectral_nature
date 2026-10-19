import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson } from "@/lib/server/http";

const PERIODS = new Set(["1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"]);

export async function GET(request: NextRequest) {
  const raw = String(request.nextUrl.searchParams.get("period") || "5Y").trim().toUpperCase();
  const period = PERIODS.has(raw) ? raw : "5Y";
  const response = await authorizedBackendFetch(`/v1/portfolio?period=${encodeURIComponent(period)}`, { timeoutMs: 45_000 });
  return forwardJson(response, "Portfolio data could not be loaded.");
}
