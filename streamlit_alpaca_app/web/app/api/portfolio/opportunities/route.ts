import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const period = String(request.nextUrl.searchParams.get("period") || "5Y").toUpperCase();
  if (!["1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"].includes(period)) {
    return noStoreJson({ error: "Portfolio period is invalid." }, { status: 400 });
  }
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/portfolio/opportunities?period=${encodeURIComponent(period)}`, { timeoutMs: 45_000 }), "List analytics are unavailable.");
  } catch {
    return noStoreJson({ error: "List analytics are unavailable." }, { status: 503 });
  }
}
