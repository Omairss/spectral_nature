import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const scope = request.nextUrl.searchParams.get("scope") || "mine";
  if (scope !== "mine" && scope !== "all") return noStoreJson({ error: "Unknown request scope." }, { status: 400 });
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/feature-requests?scope=${scope}`), "Feature requests are unavailable.");
  } catch {
    return noStoreJson({ error: "Feature requests are unavailable." }, { status: 503 });
  }
}

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  let body: unknown;
  try { body = await readJsonBody(request); }
  catch { return noStoreJson({ error: "The request could not be read." }, { status: 400 }); }
  try {
    return forwardJson(await authorizedBackendFetch("/v1/feature-requests", {
      method: "POST", body: JSON.stringify(body),
    }), "The feature request could not be sent.");
  } catch {
    return noStoreJson({ error: "The feature request could not be sent. Please retry." }, { status: 503 });
  }
}
