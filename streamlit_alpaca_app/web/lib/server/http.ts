import "server-only";

import { NextResponse } from "next/server";

export function noStoreJson(body: unknown, init: ResponseInit = {}) {
  const headers = new Headers(init.headers);
  headers.set("Cache-Control", "no-store");
  return NextResponse.json(body, { ...init, headers });
}

export async function forwardJson(response: Response, fallback: string) {
  let payload: unknown = { error: fallback };
  try {
    payload = await response.json();
  } catch {
    payload = { error: fallback };
  }
  return noStoreJson(payload, { status: response.status });
}

export function validRunId(value: string) {
  return /^agrun_[a-zA-Z0-9_-]{8,64}$/.test(value);
}

export function validUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
