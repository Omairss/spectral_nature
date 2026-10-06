"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { MetricStrip, WorkbookTabs } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";
import type { ResearchRun } from "@/lib/types";

type ZopediaPageRecord = {
  page_id: string; page_type: string; title: string; summary: string; status: string;
  version: number; updated_at: string | null; source_urls: string[];
};
type MemoryRecord = {
  document_id: string; title: string; excerpt: string; url: string; source_kind: string;
  provider: string; authority: string; published_at: string | null; as_of: string | null;
  tickers: string[]; event_tags: string[]; search_score: number | null;
};
type Workspace = {
  query: string;
  pages: ZopediaPageRecord[];
  documents: MemoryRecord[];
  proposals: Array<Record<string, unknown>>;
  maintenance_reports: Array<Record<string, unknown>>;
  counts: { pages: number; documents: number; open_proposals: number };
};

function dateTime(value: string | null | undefined) {
  const parsed = new Date(value || "");
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function statusLabel(value: string) { return value.replaceAll("_", " "); }

export default function ZopediaPage() {
  const router = useRouter();
  const { session, sessionError } = useAuthenticatedSession("/zopedia");
  const [tab, setTab] = useState("chat");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [threads, setThreads] = useState<ResearchRun[]>([]);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);

  const load = useCallback(async (nextSearch = "") => {
    await Promise.resolve();
    setError("");
    try {
      const [workspaceResponse, threadsResponse] = await Promise.all([
        fetch(`/api/zopedia/workspace?q=${encodeURIComponent(nextSearch)}&limit=60`, { cache: "no-store", credentials: "same-origin" }),
        fetch("/api/research-runs?limit=60", { cache: "no-store", credentials: "same-origin" }),
      ]);
      if (!workspaceResponse.ok) throw new Error(await responseError(workspaceResponse, "Zopedia workspace is unavailable."));
      if (!threadsResponse.ok) throw new Error(await responseError(threadsResponse, "Research threads are unavailable."));
      setWorkspace((await workspaceResponse.json()) as Workspace);
      setThreads((await threadsResponse.json()) as ResearchRun[]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Zopedia workspace is unavailable.");
    }
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(""); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);

  async function submitResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const researchQuery = query.trim();
    if (researchQuery.length < 3) return;
    setSubmitting(true); setError(""); setNotice("");
    try {
      const response = await secureMutation("/api/research-runs", {
        method: "POST",
        headers: { "Idempotency-Key": `web:${crypto.randomUUID()}` },
        body: JSON.stringify({ query: researchQuery }),
      });
      if (!response.ok) throw new Error(await responseError(response, "Research could not be started."));
      const run = (await response.json()) as ResearchRun;
      router.push(`/zopedia/runs/${encodeURIComponent(run.run_id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Research could not be started.");
      setSubmitting(false);
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(search);
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("source") as HTMLInputElement | null;
    const file = input?.files?.[0];
    if (!file) return;
    setUploading(true); setError(""); setNotice("");
    try {
      const body = new FormData(); body.set("source", file);
      const response = await secureMutation("/api/zopedia/sources/upload", { method: "POST", body });
      if (!response.ok) throw new Error(await responseError(response, "The source could not be stored."));
      const result = (await response.json()) as { source_title?: string };
      setNotice(`${result.source_title || file.name} is now available to Zopedia.`);
      form.reset();
      await load(search);
      setTab("sources");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The source could not be stored.");
    } finally { setUploading(false); }
  }

  const completed = useMemo(() => threads.filter((thread) => thread.status === "completed").length, [threads]);
  const active = useMemo(() => threads.filter((thread) => ["queued", "running", "cancel_requested"].includes(thread.status)).length, [threads]);

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;

  return (
    <div className="page-shell workbook-page zopedia-workspace">
      <header className="artifact-header workbook-heading">
        <div><p className="breadcrumb">Research <span>/</span> Knowledge workspace</p><h1>Zopedia</h1></div>
        <form className="zopedia-search" onSubmit={submitSearch}>
          <input aria-label="Search Zopedia" maxLength={300} onChange={(event) => setSearch(event.target.value)} placeholder="Search knowledge and sources" type="search" value={search} />
          <button className="quiet-button" type="submit">Search</button>
        </form>
      </header>

      <WorkbookTabs active={tab} items={[
        { key: "chat", label: "Research" }, { key: "threads", label: "Threads" },
        { key: "sources", label: "Sources" }, { key: "memory", label: "Memory" },
      ]} label="Zopedia sections" onChange={setTab} />
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {notice ? <p className="admin-notice" role="status">{notice}</p> : null}

      {tab === "chat" ? <>
        <MetricStrip items={[
          { label: "Threads", value: threads.length }, { label: "Complete", value: completed }, { label: "Active", value: active },
          { label: "Knowledge pages", value: workspace?.counts.pages ?? "—" }, { label: "Retained sources", value: workspace?.counts.documents ?? "—" },
        ]} />
        <section className="zopedia-research-grid">
          <div className="research-composer conversation-opening">
            <form onSubmit={submitResearch}>
              <label htmlFor="research-query">What do you want to understand?</label>
              <textarea autoFocus id="research-query" maxLength={4000} minLength={3} onChange={(event) => setQuery(event.target.value)} placeholder="Ask about a company, market event, sector, portfolio, or macro shift" required rows={8} value={query} />
              <div className="composer-actions"><span className="composer-context">AQL, SAA, retained evidence, and Zopedia memory</span><button className="primary-button" disabled={submitting} type="submit">{submitting ? "Starting…" : "Start research"}</button></div>
            </form>
          </div>
          <section className="workbook-panel recent-threads">
            <header className="panel-header"><div><p className="section-label">Continuity</p><h2>Recent threads</h2></div><button className="text-button" onClick={() => setTab("threads")} type="button">View all</button></header>
            <div className="thread-list">{threads.slice(0, 8).map((thread) => <Link href={`/zopedia/runs/${encodeURIComponent(thread.run_id)}`} key={thread.run_id}><span>{thread.query}</span><small>{statusLabel(thread.status)} · {dateTime(thread.updated_at)}</small></Link>)}</div>
          </section>
        </section>
      </> : null}

      {tab === "threads" ? <section className="workbook-panel">
        <header className="panel-header"><div><p className="section-label">Conversation history</p><h2>Research threads</h2></div><span>{threads.length}</span></header>
        <div className="thread-ledger">{threads.map((thread) => <Link className={thread.metadata.parent_run_id ? "child-thread" : ""} href={`/zopedia/runs/${encodeURIComponent(thread.run_id)}`} key={thread.run_id}><div><strong>{thread.query}</strong><small>{thread.metadata.parent_run_id ? "Follow-up" : "Research"} · {dateTime(thread.created_at)}</small></div><span className={`status-badge status-${thread.status}`}>{statusLabel(thread.status)}</span></Link>)}</div>
      </section> : null}

      {tab === "sources" ? <>
        <form className="workbook-panel source-upload" onSubmit={upload}>
          <header><p className="section-label">Add evidence</p><h2>Upload a source</h2></header>
          <input accept=".txt,.md,.markdown,.csv,.json,.pdf,text/plain,text/markdown,text/csv,application/json,application/pdf" name="source" required type="file" />
          <button className="primary-button" disabled={uploading} type="submit">{uploading ? "Storing…" : "Store source"}</button>
        </form>
        <section className="knowledge-grid">{workspace?.pages.map((page) => <article className="knowledge-card" key={page.page_id}><div><span>{page.page_type.replaceAll("_", " ")}</span><small>v{page.version}</small></div><h2>{page.title}</h2>{page.summary ? <p>{page.summary}</p> : null}<footer><time>{dateTime(page.updated_at)}</time>{page.source_urls[0] ? <a href={page.source_urls[0]} rel="noreferrer noopener" target="_blank">Source</a> : null}</footer></article>)}</section>
      </> : null}

      {tab === "memory" ? <>
        <section className="memory-ledger">{workspace?.documents.map((document) => <article className="memory-row" key={document.document_id}><div className="memory-meta"><span>{document.source_kind || "source"}</span><small>{document.provider || document.authority}</small></div><div><h2>{document.url ? <a href={document.url} rel="noreferrer noopener" target="_blank">{document.title}</a> : document.title}</h2>{document.excerpt ? <p>{document.excerpt}</p> : null}<footer>{dateTime(document.published_at || document.as_of)}{document.tickers.length ? ` · ${document.tickers.join(", ")}` : ""}{document.event_tags.length ? ` · ${document.event_tags.slice(0, 3).join(", ")}` : ""}</footer></div></article>)}</section>
        {session.context?.role === "admin" && workspace?.maintenance_reports.length ? <section className="workbook-panel zopedia-health"><header className="panel-header"><div><p className="section-label">Knowledge integrity</p><h2>Maintenance</h2></div><span>{workspace.proposals.length} open proposals</span></header><div className="health-run-list">{workspace.maintenance_reports.map((report, index) => <div key={String(report.run_id || index)}><strong>{String(report.status || "unknown")}</strong><span>{String(report.page_count ?? 0)} pages · {String(report.edge_count ?? 0)} links · {String(report.issue_count ?? 0)} issues</span><time>{dateTime(String(report.created_at_utc || ""))}</time></div>)}</div></section> : null}
      </> : null}
    </div>
  );
}
