import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { validateMutation } from "@/lib/server/auth";
import { forwardJson, noStoreJson } from "@/lib/server/http";

const allowedExtensions = new Set(["txt", "md", "markdown", "csv", "json", "pdf"]);

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  if (!(request.headers.get("content-type") || "").toLowerCase().startsWith("multipart/form-data;")) {
    return noStoreJson({ error: "Source upload must use multipart form data." }, { status: 415 });
  }
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (!Number.isFinite(declaredLength) || declaredLength <= 0 || declaredLength > 5_500_000) {
    return noStoreJson({ error: "Source files must be 5 MB or smaller." }, { status: 413 });
  }
  try {
    const incoming = await request.formData();
    const source = incoming.get("source");
    if (!(source instanceof File)) return noStoreJson({ error: "Choose a source file." }, { status: 400 });
    if (!source.size || source.size > 5_242_880) {
      return noStoreJson({ error: source.size ? "Source files must be 5 MB or smaller." : "The source file is empty." }, { status: source.size ? 413 : 400 });
    }
    const extension = source.name.toLowerCase().split(".").pop() || "";
    if (!allowedExtensions.has(extension)) {
      return noStoreJson({ error: "Upload a text, Markdown, CSV, JSON, or PDF source." }, { status: 400 });
    }
    const outgoing = new FormData();
    outgoing.set("source", source, source.name.slice(0, 255));
    const response = await authorizedBackendFetch("/v1/zopedia/sources/upload", {
      method: "POST",
      body: outgoing,
      timeoutMs: 60_000,
    });
    return forwardJson(response, "The source could not be stored.");
  } catch {
    return noStoreJson({ error: "The source could not be stored." }, { status: 503 });
  }
}
