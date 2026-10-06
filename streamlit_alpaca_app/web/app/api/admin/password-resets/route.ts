import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  try {
    const body = await readJsonBody(request, 4096) as Record<string, unknown>;
    const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
    if (!/^\S+@\S+\.\S+$/.test(email)) return noStoreJson({ error: "Email is invalid." }, { status: 400 });
    const response = await authorizedBackendFetch("/v1/admin/password-resets", { method: "POST", body: JSON.stringify({ email }), timeoutMs: 30_000 });
    return forwardJson(response, "Password reset could not be issued.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Password reset could not be issued.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}
