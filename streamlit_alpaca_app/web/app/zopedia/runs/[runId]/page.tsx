import { ResearchRunView } from "./research-run-view";

export default async function ResearchRunPage(context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  return <ResearchRunView runId={runId} />;
}
