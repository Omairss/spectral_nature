import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const pageId = request.nextUrl.searchParams.get("page_id") || "";
  if (!pageId || pageId.length > 240) return noStoreJson({ error: "Page identifier is invalid." }, { status: 400 });
  try {
    const response = await authorizedBackendFetch(`/v1/zopedia/pages?page_id=${encodeURIComponent(pageId)}`);
    return forwardJson(response, "Knowledge page is unavailable.");
  } catch {
    return noStoreJson({ error: "Knowledge page is unavailable." }, { status: 503 });
  }
}
