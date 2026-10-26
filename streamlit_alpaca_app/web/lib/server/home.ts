import "server-only";

import { createHash } from "node:crypto";
import { backendFetch } from "@/lib/server/backend";
import type { HomeView } from "@/lib/types";

// The page and polling gateway share the same published representation.
// Hash all visible data: ticker and Highlight publications can change independently.
export async function loadHomePublication(date: string) {
  const response = await backendFetch(`/v1/home${date ? `?date=${encodeURIComponent(date)}` : ""}`, {
    timeoutMs: 20_000, cache: "no-store",
  });
  if (!response.ok) return { home: null, etag: "", body: "", status: response.status };
  try {
    const body = await response.text();
    const home = JSON.parse(body) as HomeView;
    const etag = `"${createHash("sha256").update(body).digest("hex")}"`;
    return { home, etag, body, status: 200 };
  } catch {
    return { home: null, etag: "", body: "", status: 502 };
  }
}
