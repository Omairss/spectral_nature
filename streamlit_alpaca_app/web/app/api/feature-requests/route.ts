import { NextRequest } from "next/server";
import { validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { readJsonBody, forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(request: NextRequest) {
  const scope = request.nextUrl.searchParams.get("scope") || "mine";
  if (scope !== "mine" && scope !== "all") return noStoreJson({ error: "Unknown request scope." }, { status: 400 });
  return forwardJson(await authorizedBackendFetch(`/v1/feature-requests?scope=${scope}`), "Feature requests are unavailable.");
}

export async function POST(request: NextRequest) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const body = await readJsonBody(request);
  if (body instanceof Response) return body;
  return forwardJson(await authorizedBackendFetch("/v1/feature-requests", {
    method: "POST", body: JSON.stringify(body),
  }), "The feature request could not be sent.");
}
