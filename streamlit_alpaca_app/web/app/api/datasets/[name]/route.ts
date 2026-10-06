import { NextRequest } from "next/server";
import { readJsonBody, validateMutation } from "@/lib/server/auth";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

const WORKBENCH_DATASETS = new Set([
  "fred_dashboard",
  "market_opportunity_feed",
  "momentum_profiles",
  "option_chain",
  "option_surface",
  "option_candidates",
  "portfolio_timeseries",
  "performance_table",
  "job_status",
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export async function POST(request: NextRequest, context: { params: Promise<{ name: string }> }) {
  const rejected = await validateMutation(request);
  if (rejected) return rejected;
  const { name: rawName } = await context.params;
  const name = String(rawName || "").trim();
  if (!WORKBENCH_DATASETS.has(name)) {
    return noStoreJson({ error: "Dataset is unavailable." }, { status: 404 });
  }
  try {
    const body = await readJsonBody(request, 24_576);
    if (!isPlainObject(body) || (body.params !== undefined && !isPlainObject(body.params))) {
      return noStoreJson({ error: "Dataset parameters must be an object." }, { status: 400 });
    }
    const response = await authorizedBackendFetch(`/v1/dataset/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify({ params: body.params || {} }),
      timeoutMs: name === "fred_dashboard" ? 60_000 : 45_000,
    });
    return forwardJson(response, "Dataset could not be loaded.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Dataset could not be loaded.";
    return noStoreJson({ error: message }, { status: 400 });
  }
}
