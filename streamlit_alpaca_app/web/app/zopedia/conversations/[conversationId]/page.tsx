import { ResearchConversationView } from "../../runs/[runId]/research-run-view";

export default async function ResearchConversationPage(context: { params: Promise<{ conversationId: string }> }) {
  const { conversationId } = await context.params;
  return <ResearchConversationView key={conversationId} conversationId={conversationId} />;
}
