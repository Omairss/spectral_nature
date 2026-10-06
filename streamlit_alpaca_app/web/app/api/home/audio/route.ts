import { NextRequest } from "next/server";
import { backendFetch } from "@/lib/server/backend";
import { noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const rawDate = String(request.nextUrl.searchParams.get("date") || "").trim();
  if (rawDate && !/^\d{4}-\d{2}-\d{2}$/.test(rawDate)) {
    return noStoreJson({ error: "Replay date is invalid." }, { status: 400 });
  }
  try {
    const response = await backendFetch(
      `/v1/home/audio${rawDate ? `?date=${encodeURIComponent(rawDate)}` : ""}`,
      { timeoutMs: 20_000 },
    );
    if (!response.ok || !response.body) {
      return noStoreJson({ error: "Home audio is unavailable." }, { status: response.status || 503 });
    }
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Cache-Control": rawDate ? "public, max-age=3600, stale-while-revalidate=86400" : "public, max-age=300",
        "Content-Type": response.headers.get("content-type") || "audio/mpeg",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return noStoreJson({ error: "Home audio is unavailable." }, { status: 503 });
  }
}
