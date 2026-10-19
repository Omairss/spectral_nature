import { NextRequest } from "next/server";
import { validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { readJsonBody, forwardJson } from "@/lib/server/http";

export async function PATCH(request: NextRequest) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  const body = await readJsonBody(request, 1_048_576);
  if (body instanceof Response) return body;
  const response = await authorizedBackendFetch("/v1/admin/llm", {
    method: "PATCH", body: JSON.stringify(body), timeoutMs: 30_000,
  });
  return forwardJson(response, "LLM settings could not be saved.");
}
