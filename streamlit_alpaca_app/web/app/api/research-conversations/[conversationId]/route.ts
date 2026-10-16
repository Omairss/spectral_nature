import { NextRequest } from "next/server";
import { authorizedBackendFetch } from "@/lib/server/backend";
import { forwardJson, noStoreJson } from "@/lib/server/http";

export async function GET(_request: NextRequest, context: { params: Promise<{ conversationId: string }> }) {
  const { conversationId } = await context.params;
  if (!/^zthread_[a-zA-Z0-9_-]{8,64}$/.test(conversationId)) return noStoreJson({ error: "Conversation not found." }, { status: 404 });
  try {
    return forwardJson(await authorizedBackendFetch(`/v1/research-conversations/${encodeURIComponent(conversationId)}`), "Conversation is unavailable.");
  } catch {
    return noStoreJson({ error: "Conversation is unavailable." }, { status: 503 });
  }
}
