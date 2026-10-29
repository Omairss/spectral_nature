"use client";

import { useEffect, useState } from "react";
import { fetchDataset } from "@/lib/client/api";
import { Markdown } from "@/components/markdown";

type PageSummary = {
  status: string;
  headline: string;
  summary_markdown: string;
  watch_items: string[];
  materialized?: { generated_at_utc?: string; run_id?: string };
};

export function PageResearchSummary({ surface, label }: { surface: string; label: string }) {
  const [summary, setSummary] = useState<PageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    let pending = false;
    async function refresh() {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const result = await fetchDataset<PageSummary>("page_agentic_summary", { surface });
        if (!active) return;
        const next = result.payload;
        if (next.status === "ok" && (next.headline || next.summary_markdown)) {
          setSummary(next);
          setFailed(false);
        } else {
          setFailed(true);
        }
      } catch {
        if (active) setFailed(true);
      } finally {
        pending = false;
        if (active) setLoading(false);
      }
    }
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 300_000);
    const onVisible = () => { void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [surface, retry]);

  const generatedAt = summary?.materialized?.generated_at_utc;
  const date = generatedAt ? new Date(generatedAt) : null;
  return (
    <section className="workbook-panel page-research-summary" aria-label={label} aria-busy={loading}>
      <header className="panel-header">
        <div><p className="section-label">{label}</p>{summary?.headline ? <h2>{summary.headline}</h2> : null}</div>
        {date && Number.isFinite(date.getTime()) ? <time dateTime={generatedAt}>{date.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "America/New_York" })} ET</time> : null}
      </header>
      {summary?.summary_markdown ? <Markdown>{summary.summary_markdown}</Markdown> : null}
      {summary?.watch_items?.length ? <div><h3>Watch next</h3><Markdown>{summary.watch_items.map((item) => `- ${item}`).join("\n")}</Markdown></div> : null}
      {loading && !summary ? <p className="loading-label" role="status">Loading {label.toLowerCase()}…</p> : null}
      {failed ? <div className="summary-retry"><span className="subtle-error" role="status">{summary ? "Summary refresh failed." : "Summary unavailable."}</span><button type="button" onClick={() => setRetry((value) => value + 1)}>Retry</button></div> : null}
    </section>
  );
}
