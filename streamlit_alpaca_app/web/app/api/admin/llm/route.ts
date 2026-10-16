import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function PATCH(request: NextRequest) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  try {
    const body = await readJsonBody(request, 1_048_576);
    const response = await authorizedBackendFetch("/v1/admin/llm", {
      method: "PATCH", body: JSON.stringify(body), timeoutMs: 30_000,
    });
    return forwardJson(response, "LLM settings could not be saved.");
  } catch (error) {
    return noStoreJson({ error: error instanceof Error ? error.message : "LLM settings could not be saved." }, { status: 400 });
  }
}
