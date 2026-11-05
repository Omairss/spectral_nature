import { AttentionEvent } from "@/components/attention-event";
import { loadAttentionEvent } from "@/lib/server/attention-event";

export default async function Page({ params }: { params: Promise<{ version: string; event: string }> }) {
  const { version, event } = await params;
  return <AttentionEvent {...await loadAttentionEvent(version, event)} />;
}
