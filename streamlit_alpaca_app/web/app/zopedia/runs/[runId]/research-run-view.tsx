"use client";

import Link from "next/link";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/markdown";
import { responseError, secureMutation } from "@/lib/client/api";
import type { ResearchEvent, ResearchEvents, ResearchRun } from "@/lib/types";

const terminal = new Set(["completed", "failed", "cancelled"]);

function statusLabel(status: ResearchRun["status"]) {
  return status.replace("_", " ");
}

export function ResearchRunView({ runId }: { runId: string }) {
  const router = useRouter();
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [followUp, setFollowUp] = useState("");
  const [followingUp, setFollowingUp] = useState(false);
  const sequenceRef = useRef(0);

  const refresh = useCallback(async () => {
    const runResponse = await fetch(`/api/research-runs/${encodeURIComponent(runId)}`, { cache: "no-store" });
    if (runResponse.status === 401 || runResponse.status === 403) {
      router.replace(`/login?next=${encodeURIComponent(`/zopedia/runs/${runId}`)}`);
      return null;
    }
    if (!runResponse.ok) throw new Error(await responseError(runResponse, "Research run is unavailable."));
    const nextRun = (await runResponse.json()) as ResearchRun;
    setRun(nextRun);

    const eventResponse = await fetch(
      `/api/research-runs/${encodeURIComponent(runId)}/events?after_sequence=${sequenceRef.current}`,
      { cache: "no-store" },
    );
    if (eventResponse.ok) {
      const payload = (await eventResponse.json()) as ResearchEvents;
      if (payload.events.length) {
        sequenceRef.current = Math.max(sequenceRef.current, payload.last_event_sequence);
        setEvents((current) => {
          const merged = new Map(current.map((event) => [event.event_id, event]));
          for (const event of payload.events) merged.set(event.event_id, event);
          return [...merged.values()].sort((left, right) => left.sequence - right.sequence);
        });
      }
    }
    return nextRun;
  }, [router, runId]);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const nextRun = await refresh();
        if (active && nextRun && !terminal.has(nextRun.status)) timer = setTimeout(poll, 1800);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Research run is unavailable.");
      }
    }
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  async function cancel() {
    setCancelling(true);
    try {
      const response = await secureMutation(`/api/research-runs/${encodeURIComponent(runId)}/cancel`, {
        method: "POST",
        body: "{}",
      });
      if (!response.ok) throw new Error(await responseError(response, "Cancellation failed."));
      setRun((await response.json()) as ResearchRun);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Cancellation failed.");
    } finally {
      setCancelling(false);
    }
  }

  async function submitFollowUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = followUp.trim();
    if (query.length < 3 || followingUp) return;
    setFollowingUp(true); setError("");
    try {
      const response = await secureMutation("/api/research-runs", {
        method: "POST",
        headers: { "Idempotency-Key": `web:${crypto.randomUUID()}` },
        body: JSON.stringify({ query, parent_run_id: runId }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Follow-up research could not be started."));
      const child = (await response.json()) as ResearchRun;
      router.push(`/zopedia/runs/${encodeURIComponent(child.run_id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Follow-up research could not be started.");
      setFollowingUp(false);
    }
  }

  if (!run && !error) return <div className="page-shell"><p className="loading-label">Loading research…</p></div>;

  return (
    <div className="page-shell run-page">
      <header className="artifact-header run-heading">
        <div>
          <p className="breadcrumb"><Link href="/zopedia">Zopedia</Link> <span>/</span> Research</p>
          <h1>{run?.query || "Research run"}</h1>
        </div>
        {run ? <span className={`status-badge status-${run.status}`}>{statusLabel(run.status)}</span> : null}
      </header>

      {error ? <p className="form-error" role="alert">{error}</p> : null}

      {run && !terminal.has(run.status) ? (
        <section className="run-progress" aria-live="polite">
          <progress max={1} value={events.at(-1)?.progress || 0} />
          <div>
            <strong>{events.at(-1)?.message || "Research run queued."}</strong>
            <button className="quiet-button" disabled={cancelling} onClick={cancel} type="button">
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          </div>
        </section>
      ) : null}

      {run?.result?.answer_markdown ? (
        <article className="answer-panel">
          <div className="answer-meta">
            <span>Confidence · {run.result.confidence}</span>
            {run.result.model ? <span>{run.result.model}</span> : null}
          </div>
          <Markdown>{run.result.answer_markdown}</Markdown>
          {run.result.citations.length ? (
            <section className="sources">
              <h2>Sources</h2>
              <ul>
                {run.result.citations.map((citation) => (
                  <li key={citation.url}><a href={citation.url} rel="noreferrer noopener" target="_blank">{citation.label}</a></li>
                ))}
              </ul>
            </section>
          ) : null}
          {run.result.limitations.length ? (
            <section className="limitations">
              <h2>Limits</h2>
              <ul>{run.result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ) : null}
        </article>
      ) : run && terminal.has(run.status) ? (
        <section className="empty-state">
          <h2>{run.status === "cancelled" ? "Research cancelled" : "Research did not complete"}</h2>
          {run.status === "failed" ? <p>The run ended safely without a usable answer.</p> : null}
        </section>
      ) : null}

      {run?.status === "completed" ? (
        <section className="research-composer follow-up-composer">
          <form onSubmit={submitFollowUp}>
            <label htmlFor="follow-up">Continue this research</label>
            <textarea id="follow-up" maxLength={4000} minLength={3} onChange={(event) => setFollowUp(event.target.value)} placeholder="Ask a follow-up using this result and its evidence as context" required rows={4} value={followUp} />
            <div className="composer-actions"><span className="composer-context">This answer will be carried into the next run.</span><button className="primary-button" disabled={followingUp} type="submit">{followingUp ? "Starting…" : "Continue"}</button></div>
          </form>
        </section>
      ) : null}

      {events.length ? (
        <details className="journal" open={Boolean(run && !terminal.has(run.status))}>
          <summary><span>Research journal</span><small>{events.length} events</small></summary>
          <ol>
            {events.map((event) => (
              <li key={event.event_id}>
                <span>{event.stage.replaceAll("_", " ")}</span>
                <p>{event.message}</p>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}
