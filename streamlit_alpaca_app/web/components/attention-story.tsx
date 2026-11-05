import type { HomeBeat } from "@/lib/types";

export function AttentionStory({ beat }: { beat: HomeBeat }) {
  return <>
    <h3>{beat.title}</h3>
    {beat.summary ? <p>{beat.summary}</p> : null}
    {beat.what_changed ? <p>{beat.what_changed}</p> : null}
    {beat.why ? <p>{beat.why}</p> : null}
    {beat.business_context ? <p className="business-context">{beat.business_context}</p> : null}
    {beat.watch_next ? <p><strong>Watch next: </strong>{beat.watch_next}</p> : null}
  </>;
}
