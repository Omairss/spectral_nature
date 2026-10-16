"use client";

import type { FormEvent, KeyboardEvent } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { responseError, secureMutation } from "@/lib/client/api";
import type { ResearchRun, Session } from "@/lib/types";
import { Icon } from "./icons";

function contextForPath(pathname: string) {
  if (pathname.startsWith("/broad-economy")) return "Broad Economy";
  if (pathname.startsWith("/market-explorer")) return "Market Explorer";
  if (pathname.startsWith("/portfolio-performance")) return "Portfolio Performance";
  if (pathname.startsWith("/portfolio")) return "Portfolio";
  if (pathname.startsWith("/options")) return "Option Strategizer";
  if (pathname.startsWith("/trading-agent")) return "Trading Agent";
  if (pathname.startsWith("/admin")) return "Admin";
  if (pathname.startsWith("/stock-investigator")) return "Stock Investigator";
  if (pathname.startsWith("/zopedia/runs/")) return "Open research run";
  if (pathname.startsWith("/zopedia")) return "Zopedia";
  return "Daily Brief";
}

export function AgentRail({ session }: { session: Session | null }) {
  const pathname = usePathname();
  const router = useRouter();
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<{ query: string; key: string; body: string } | null>(null);
  const context = useMemo(() => contextForPath(pathname), [pathname]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = draft.trim();
    if (!query || submitting) return;
    if (!session?.authenticated) {
      router.push(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      if (pending.current?.query !== query) pending.current = {
        query, key: `web:${crypto.randomUUID()}`,
        body: JSON.stringify({ query, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          asof_time_utc: new Date().toISOString(),
          visible_artifacts: [{ kind: "workspace", path: pathname, query: window.location.search, title: context }] }),
      };
      const response = await secureMutation("/api/research-runs", {
        method: "POST",
        headers: { "Idempotency-Key": pending.current.key },
        body: pending.current.body,
      });
      if (!response.ok) {
        setError(await responseError(response, "Research could not be started."));
        return;
      }
      const run = (await response.json()) as ResearchRun;
      setDraft("");
      pending.current = null;
      router.push(`/zopedia/conversations/${encodeURIComponent(run.conversation_id)}`);
    } catch {
      setError("Research could not be started.");
    } finally {
      setSubmitting(false);
    }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <aside className="agent-rail" aria-label="Research conversation">
      <header className="agent-header">
        <div>
          <span className="agent-presence" />
          <strong>Research</strong>
        </div>
        <button aria-label="New conversation" className="icon-button" onClick={() => router.push("/zopedia")} type="button">
          <Icon name="plus" />
        </button>
      </header>

      <div className="agent-body">
        <div className="context-block">
          <span>Context</span>
          <strong>{context}</strong>
        </div>
      </div>

      <form className="agent-composer" onSubmit={submit}>
        <textarea
          aria-label="Ask Spectral Nature"
          maxLength={4000}
          minLength={1}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={keyDown}
          placeholder="Ask Spectral Nature…"
          rows={3}
          value={draft}
        />
        <div className="agent-composer-foot">
          {error ? <span className="composer-error" role="alert">{error}</span> : <span />}
          <button aria-label="Send" className="send-button" disabled={!draft.trim() || submitting} type="submit">
            <Icon name="arrow-up" />
          </button>
        </div>
      </form>
    </aside>
  );
}
