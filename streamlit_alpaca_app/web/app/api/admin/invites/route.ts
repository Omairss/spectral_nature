import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function POST(request: NextRequest) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  try {
    const body = await readJsonBody(request, 8192) as Record<string, unknown>;
    const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
    const role = typeof body?.role === "string" ? body.role.trim().toLowerCase() : "";
    const share = body?.share_fraction === null || body?.share_fraction === undefined ? null : Number(body.share_fraction);
    if (!/^\S+@\S+\.\S+$/.test(email) || !["investor", "viewer", "admin"].includes(role) || (share !== null && (!Number.isFinite(share) || share < 0 || share > 1))) {
      return noStoreJson({ error: "Invite details are invalid." }, { status: 400 });
    }
    const response = await authorizedBackendFetch("/v1/admin/invites", { method: "POST", body: JSON.stringify({ email, role, share_fraction: share }), timeoutMs: 30_000 });
    return forwardJson(response, "Invite could not be created.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invite could not be created.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}
