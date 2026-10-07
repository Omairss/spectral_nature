import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const query = (request.nextUrl.searchParams.get("q") || "").trim().slice(0, 300);
  const parsed = Number(request.nextUrl.searchParams.get("limit") || 60);
  const limit = Number.isFinite(parsed) ? Math.max(1, Math.min(Math.floor(parsed), 100)) : 60;
  const folder = (request.nextUrl.searchParams.get("folder") || "").slice(0, 300);
  const rawOffset = Number(request.nextUrl.searchParams.get("offset") || 0);
  const offset = Number.isFinite(rawOffset) ? Math.min(100_000, Math.max(0, Math.floor(rawOffset))) : 0;
  try {
    const response = await authorizedBackendFetch(`/v1/zopedia/workspace?q=${encodeURIComponent(query)}&limit=${limit}&folder=${encodeURIComponent(folder)}&offset=${offset}`);
    return forwardJson(response, "Zopedia workspace is unavailable.");
  } catch {
    return noStoreJson({ error: "Zopedia workspace is unavailable." }, { status: 503 });
  }
}
