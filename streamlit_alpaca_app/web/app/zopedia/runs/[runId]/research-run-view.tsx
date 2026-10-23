"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Markdown } from "@/components/markdown";
import { Visualization } from "@/components/visualizations/visualization";
import { responseError, secureMutation } from "@/lib/client/api";
import type { ResearchConversation, ResearchEvent, ResearchEvents, ResearchPublicUpdate, ResearchRun } from "@/lib/types";

const terminal = new Set(["completed", "failed", "cancelled"]);

function publicUpdate(event: ResearchEvent): ResearchPublicUpdate | null {
  const value = event.payload.public_update;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const fields = value as Record<string, unknown>;
  return { summary: String(fields.summary || ""), finding: String(fields.finding || ""), next_step: String(fields.next_step || "") };
}

function ResearchActivity({ events, active }: { events: ResearchEvent[]; active: boolean }) {
  const activity = [...events].reverse().find((event) => typeof event.payload.activity_label === "string")?.payload.activity_label;
  const updates = events.flatMap((event) => {
    const update = publicUpdate(event);
    return update && (update.summary || update.finding) ? [{ event, update }] : [];
  });
  const sources = new Map<string, string>();
  for (const event of events) {
    if (!Array.isArray(event.payload.source_links)) continue;
    for (const source of event.payload.source_links) {
      if (source && typeof source === "object" && typeof source.url === "string" && /^https?:\/\//.test(source.url)) {
        sources.set(source.url, String(source.label || source.url));
      }
    }
  }
  if (!updates.length && !sources.size) return active ? <p className="research-current" role="status">{String(activity || (events.some((event) => event.stage === "running") ? "Preparing research…" : "Queued"))}</p> : null;
  const latest = updates.at(-1)?.update;
  return (
    <details className="research-activity" open={active || undefined}>
      <summary>{active ? String(activity || "Researching") : "Research activity"}{sources.size ? <span>{sources.size} sources</span> : null}</summary>
      <ol>
        {updates.map(({ event, update }) => <li key={event.event_id}>
          {update.summary ? <p>{update.summary}</p> : null}
          {update.finding ? <p className="research-finding">{update.finding}</p> : null}
        </li>)}
      </ol>
      {active && latest?.next_step ? <p className="research-next" aria-live="polite">{latest.next_step}</p> : null}
      {sources.size ? <ul className="research-sources">{[...sources].map(([url, label]) => <li key={url}><a href={url} rel="noreferrer noopener" target="_blank">{label}</a></li>)}</ul> : null}
    </details>
  );
}

export function ResearchRunView({ runId }: { runId: string }) {
  return <ResearchConversationView key={runId} initialRunId={runId} />;
}

export function ResearchConversationView({ conversationId, initialRunId }: { conversationId?: string; initialRunId?: string }) {
  const router = useRouter();
  const [conversation, setConversation] = useState<ResearchConversation | null>(null);
  const [journals, setJournals] = useState<Record<string, ResearchEvent[]>>({});
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [pollRevision, setPollRevision] = useState(0);
  const resolvedId = useRef(conversationId || "");
  const journalCache = useRef<Record<string, ResearchEvent[]>>({});
  const pending = useRef<{ query: string; key: string; body: string } | null>(null);
  const transcript = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read<T>(url: string): Promise<T> {
      const response = await fetch(url, { cache: "no-store", signal: controller.signal });
      if (response.status === 401 || response.status === 403) {
        router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
        throw new Error("Sign in to view this conversation.");
      }
      if (!response.ok) throw new Error(await responseError(response, "Conversation is unavailable."));
      return response.json() as Promise<T>;
    }
    async function poll() {
      try {
        if (!resolvedId.current && initialRunId) {
          const run = await read<ResearchRun>(`/api/research-runs/${encodeURIComponent(initialRunId)}`);
          resolvedId.current = run.conversation_id;
          if (!resolvedId.current) throw new Error("Conversation is unavailable.");
          window.history.replaceState(null, "", `/zopedia/conversations/${encodeURIComponent(resolvedId.current)}`);
        }
        const next = await read<ResearchConversation>(`/api/research-conversations/${encodeURIComponent(resolvedId.current)}`);
        const runs = next.turns.flatMap((turn) => turn.attempts);
        await Promise.all(runs.map(async (run) => {
          let events = journalCache.current[run.run_id] || [];
          while ((events.at(-1)?.sequence || 0) < run.last_event_sequence) {
            const payload = await read<ResearchEvents>(`/api/research-runs/${encodeURIComponent(run.run_id)}/events?after_sequence=${events.at(-1)?.sequence || 0}`);
            if (!payload.events.length) break;
            const merged = new Map(events.map((event) => [event.event_id, event]));
            for (const event of payload.events) merged.set(event.event_id, event);
            events = [...merged.values()].sort((left, right) => left.sequence - right.sequence);
          }
          journalCache.current[run.run_id] = events;
        }));
        if (controller.signal.aborted) return;
        setConversation(next);
        setJournals({ ...journalCache.current });
        setError("");
        timer = setTimeout(poll, runs.some((run) => !terminal.has(run.status)) ? 1800 : 10000);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Conversation is unavailable.");
      }
    }
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [router, initialRunId, conversationId, pollRevision]);

  const runs = conversation?.turns.flatMap((turn) => turn.attempts) || [];
  const activeRun = runs.find((run) => !terminal.has(run.status));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (text.length < 1 || submitting || activeRun || !conversation) return;
    if (pending.current?.query !== text) pending.current = {
      query: text, key: `web:${crypto.randomUUID()}`,
      body: JSON.stringify({ query: text, conversation_id: conversation.conversation_id,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, asof_time_utc: new Date().toISOString() }),
    };
    setSubmitting(true); setError("");
    try {
      const response = await secureMutation("/api/research-runs", { method: "POST", headers: { "Idempotency-Key": pending.current.key }, body: pending.current.body });
      if (!response.ok) throw new Error(await responseError(response, "Message could not be sent."));
      const run = await response.json() as ResearchRun;
      setConversation((current) => current ? { ...current, turns: [...current.turns, { turn_id: run.turn_id, query: run.query, created_at: run.created_at, attempts: [run] }] } : current);
      setQuery(""); pending.current = null; setPollRevision((value) => value + 1);
      requestAnimationFrame(() => {
        const turn = transcript.current?.lastElementChild;
        const canvas = transcript.current?.closest<HTMLElement>(".canvas-scroll");
        if (turn && canvas) canvas.scrollTo({ top: canvas.scrollTop + turn.getBoundingClientRect().top - canvas.getBoundingClientRect().top, behavior: "smooth" });
      });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Message could not be sent."); }
    finally { setSubmitting(false); }
  }

  async function cancel() {
    if (!activeRun) return;
    setCancelling(true);
    try {
      const response = await secureMutation(`/api/research-runs/${encodeURIComponent(activeRun.run_id)}/cancel`, { method: "POST", body: "{}" });
      if (!response.ok) throw new Error(await responseError(response, "Cancellation failed."));
      setPollRevision((value) => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Cancellation failed."); }
    finally { setCancelling(false); }
  }

  return <div className="page-shell run-page conversation-page">
    <header className="artifact-header run-heading">
      <div><p className="breadcrumb"><Link href="/zopedia">Zopedia</Link></p><h1>{conversation?.title || "Conversation"}</h1></div>
      <Link className="quiet-button" href="/zopedia">New conversation</Link>
    </header>
    {!conversation && !error ? <p className="loading-label">Loading conversation…</p> : null}
    <div className="conversation-transcript" ref={transcript}>
      {conversation?.turns.map((turn) => {
        const run = turn.attempts.at(-1)!;
        const active = !terminal.has(run.status);
        return <section className="conversation-turn" key={turn.turn_id}>
          <div className="conversation-question"><Markdown>{turn.query}</Markdown></div>
          <ResearchActivity events={journals[run.run_id] || []} active={active} />
          {run.result?.answer_markdown ? <article className="answer-panel">
            <Markdown>{run.result.answer_markdown}</Markdown>
            {run.result.visualizations?.length ? <div className="research-charts">
              {run.result.visualizations.map((artifact) => <figure className="research-chart" key={artifact.artifact_id}>
                <figcaption>{artifact.title}</figcaption>
                <Visualization artifact={artifact} showTitle={false} preserveAxisTitles />
              </figure>)}
            </div> : null}
            {run.result.citations.length ? <details className="sources conversation-citations"><summary>Sources · {run.result.citations.length}</summary><ul>{run.result.citations.map((source) => <li key={source.url}><a href={source.url} rel="noreferrer noopener" target="_blank">{source.label}</a></li>)}</ul></details> : null}
          </article> : !active ? <p className="research-failure" role="status">{run.status === "cancelled" ? "Research cancelled." : "This reply did not complete. You can continue below."}</p> : null}
          {active ? <div className="conversation-run-controls"><span className="research-working" aria-label="Research in progress" /><button className="quiet-button" disabled={cancelling || run.status === "cancel_requested"} onClick={cancel} type="button">{cancelling || run.status === "cancel_requested" ? "Cancelling…" : "Stop"}</button></div> : null}
        </section>;
      })}
    </div>
    {error ? <p className="form-error" role="alert">{error} <button className="quiet-button" onClick={() => setPollRevision((value) => value + 1)} type="button">Retry</button></p> : null}
    {conversation ? <section className="research-composer conversation-composer"><form onSubmit={submit}>
      <label className="visually-hidden" htmlFor="conversation-message">Message Zopedia</label>
      <textarea id="conversation-message" maxLength={4000} minLength={1} onChange={(event) => setQuery(event.target.value)} placeholder="Message Zopedia" required rows={3} value={query} />
      <div className="composer-actions"><button className="primary-button" disabled={submitting || Boolean(activeRun) || !query.trim()} type="submit">{submitting ? "Sending…" : "Send"}</button></div>
    </form></section> : null}
  </div>;
}
