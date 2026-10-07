import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(_request: Request, context: { params: Promise<{ pageId: string }> }) {
  const { pageId } = await context.params;
  if (!pageId || pageId.length > 240) return noStoreJson({ error: "Page identifier is invalid." }, { status: 400 });
  try {
    const response = await authorizedBackendFetch(`/v1/zopedia/pages/${encodeURIComponent(pageId)}`);
    return forwardJson(response, "Knowledge page is unavailable.");
  } catch {
    return noStoreJson({ error: "Knowledge page is unavailable." }, { status: 503 });
  }
}
