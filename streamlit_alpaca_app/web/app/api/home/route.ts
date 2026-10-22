import { NextRequest } from "next/server";
import { loadHomePublication } from "@/lib/server/home";
import { noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const rawDate = String(request.nextUrl.searchParams.get("date") || "").trim();
  const date = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? rawDate : "";
  try {
    const publication = await loadHomePublication(date);
    if (!publication.home || publication.home.status !== "available") {
      return noStoreJson({ status: "unavailable" }, { status: publication.status === 200 ? 503 : publication.status });
    }
    const headers = { "ETag": publication.etag, "Cache-Control": "no-cache", "Content-Type": "application/json" };
    const matches = (request.headers.get("If-None-Match") || "").split(",").some(value =>
      value.trim().replace(/^W\//, "") === publication.etag || value.trim() === "*");
    return matches ? new Response(null, { status: 304, headers })
      : new Response(publication.body, { headers });
  } catch {
    return noStoreJson({ status: "unavailable" }, { status: 503 });
  }
}
