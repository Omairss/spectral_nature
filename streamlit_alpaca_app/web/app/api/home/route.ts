import { NextRequest } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const rawDate = String(request.nextUrl.searchParams.get("date") || "").trim();
  const date = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : "";
  try {
    const response = await backendFetch(`/v1/home${date ? `?date=${encodeURIComponent(date)}` : ""}`, { timeoutMs: 20_000 });
    if (!response.ok) return noStoreJson({ status: "unavailable" }, { status: response.status });
    const result = noStoreJson(await response.json());
    result.headers.set(
      "Cache-Control",
      date ? "public, max-age=3600, stale-while-revalidate=86400" : "public, max-age=30, stale-while-revalidate=120",
    );
    return result;
  } catch {
    return noStoreJson({ status: "unavailable" }, { status: 503 });
  }
}
