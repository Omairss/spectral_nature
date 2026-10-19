import "server-only";

import { NextResponse } from "next/server";

export function noStoreJson(body: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Cache-Control", "no-store");
  return NextResponse.json(body, { ...init, headers });
}

export async function forwardJson(response: Response, fallback: string) {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return noStoreJson({ error: fallback }, { status: response.ok ? 502 : response.status });
  }
  return noStoreJson(payload, { status: response.status });
}

export async function readJsonBody(request: Request, maxBytes = 16_384): Promise<unknown> {
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    return noStoreJson({ error: "Request body is too large." }, { status: 413 });
  }
  try {
    const text = await request.text();
    if (Buffer.byteLength(text, "utf8") > maxBytes) {
      return noStoreJson({ error: "Request body is too large." }, { status: 413 });
    }
    return JSON.parse(text);
  } catch {
    return noStoreJson({ error: "Request body must be valid JSON." }, { status: 400 });
  }
}

export function validRunId(value: string) {
  return /^agrun_[a-zA-Z0-9_-]{8,64}$/.test(value);
}

export function validUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
