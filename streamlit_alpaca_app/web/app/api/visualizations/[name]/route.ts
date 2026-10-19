import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { validateMutation } from "@/lib/server/auth";
import { readJsonBody, forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest, context: { params: Promise<{ name: string }> }) {
  const rejection = await validateMutation(request);
  if (rejection) return rejection;
  const { name } = await context.params;
  if (!/^[a-z0-9_]{1,64}$/.test(name)) {
    return noStoreJson({ error: "Visualization name is invalid." }, { status: 400 });
  }
  const body = await readJsonBody(request);
  if (body instanceof Response) return body;
  const params = body && typeof body === "object" && !Array.isArray(body)
    ? (body as Record<string, unknown>).params
    : null;
  if (!params || typeof params !== "object" || Array.isArray(params)) {
    return noStoreJson({ error: "Visualization parameters must be an object." }, { status: 400 });
  }
  const response = await authorizedBackendFetch(`/v1/visualization/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify({ params }),
  });
  return forwardJson(response, "Visualization could not be loaded.");
}
