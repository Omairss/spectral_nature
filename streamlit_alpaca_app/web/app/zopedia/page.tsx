"use client";

import Link from "next/link";
import { type FormEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Markdown } from "@/components/markdown";
import { Icon } from "@/components/icons";
import { MetricStrip, WorkbookTabs } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";
import type { ResearchRun, ResearchConversationSummary } from "@/lib/types";

type ZopediaPageRecord = {
  page_id: string; page_type: string; title: string; summary: string; status: string;
  version: number; updated_at: string | null; source_urls: string[];
  slug: string;
};
type KnowledgePage = ZopediaPageRecord & { body_markdown: string; outgoing_links: string[]; links: Array<ZopediaPageRecord & { direction: string }> };
type MemoryRecord = {
  document_id: string; title: string; excerpt: string; url: string; source_kind: string;
  provider: string; authority: string; published_at: string | null; as_of: string | null;
  tickers: string[]; event_tags: string[]; search_score: number | null;
};
type Workspace = {
  query: string;
  pages: ZopediaPageRecord[];
  source_pages: ZopediaPageRecord[];
  folders: Array<{ folder_id: string; parent_folder_id: string; name: string; path: string; folder_type: string }>;
  folder: string; folder_name: string; offset: number; has_more: boolean;
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

function ZopediaWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = params.get("tab") || (params.get("page") ? "knowledge" : "chat");
  const searchQuery = params.get("q") || "";
  const folder = params.get("folder") || "";
  const pageId = params.get("page") || "";
  const offset = Number(params.get("offset") || 0);
  const { session, sessionError } = useAuthenticatedSession("/zopedia");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [threads, setThreads] = useState<ResearchConversationSummary[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<KnowledgePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState("");
  const workspaceRequest = useRef(0);
  const reader = useRef<HTMLElement>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const pendingMessage = useRef<{ query: string; key: string; body: string } | null>(null);
  const [uploading, setUploading] = useState(false);

  function navigate(next: { tab?: string; q?: string; folder?: string; page?: string; offset?: number }) {
    const updated = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value === "" || value === 0) updated.delete(key); else updated.set(key, String(value));
    }
    router.replace(`/zopedia?${updated.toString()}`, { scroll: false });
  }
  function setTab(next: string) { navigate({ tab: next, page: "", offset: 0, folder: "" }); }

  const load = useCallback(async (nextSearch = "", nextFolder = "", nextOffset = 0) => {
    await Promise.resolve();
    const requestId = ++workspaceRequest.current;
    setLoading(true);
    setWorkspace(null);
    setWorkspaceError("");
    try {
      const workspaceResponse = await fetch(`/api/zopedia/workspace?q=${encodeURIComponent(nextSearch)}&folder=${encodeURIComponent(nextFolder)}&offset=${nextOffset}&limit=60`, { cache: "no-store", credentials: "same-origin" });
      if (!workspaceResponse.ok) throw new Error(await responseError(workspaceResponse, "Zopedia workspace is unavailable."));
      const result = (await workspaceResponse.json()) as Workspace;
      if (requestId === workspaceRequest.current) setWorkspace(result);
    } catch (reason) {
      if (requestId === workspaceRequest.current) setWorkspaceError(reason instanceof Error ? reason.message : "Zopedia workspace is unavailable.");
    } finally { if (requestId === workspaceRequest.current) setLoading(false); }
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(searchQuery, folder, offset); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session, searchQuery, folder, offset]);

  useEffect(() => {
    if (!session?.authenticated) return;
    const controller = new AbortController();
    fetch("/api/research-conversations?limit=60", { cache: "no-store", credentials: "same-origin", signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error(await responseError(response, "Conversations are unavailable.")); return response.json() as Promise<ResearchConversationSummary[]>; })
      .then(setThreads).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Conversations are unavailable."); });
    return () => controller.abort();
  }, [session]);

  useEffect(() => {
    if (!session?.authenticated || !pageId) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => { setPageLoading(true); setPageError(""); setPage(null);
      fetch(`/api/zopedia/pages?page_id=${encodeURIComponent(pageId)}`, { cache: "no-store", signal: controller.signal })
        .then(async (response) => { if (!response.ok) throw new Error(await responseError(response, "Knowledge page is unavailable.")); return response.json() as Promise<KnowledgePage>; })
        .then(setPage).catch((reason) => { if (!controller.signal.aborted) setPageError(reason instanceof Error ? reason.message : "Knowledge page is unavailable."); })
        .finally(() => { if (!controller.signal.aborted) setPageLoading(false); });
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [pageId, session]);

  useEffect(() => {
    if (page?.page_id) reader.current?.scrollIntoView({ block: "start" });
  }, [page?.page_id]);

  async function submitResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const researchQuery = query.trim();
    if (!researchQuery) return;
    setSubmitting(true); setError(""); setNotice("");
    try {
      if (pendingMessage.current?.query !== researchQuery) pendingMessage.current = {
        query: researchQuery, key: `web:${crypto.randomUUID()}`,
        body: JSON.stringify({ query: researchQuery, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          asof_time_utc: new Date().toISOString(), visible_artifacts: page?.page_id ? [{ kind: "zopedia_page", page_id: page.page_id }] : [] }),
      };
      const response = await secureMutation("/api/research-runs", {
        method: "POST", headers: { "Idempotency-Key": pendingMessage.current.key }, body: pendingMessage.current.body,
      });
      if (!response.ok) throw new Error(await responseError(response, "Research could not be started."));
      const run = (await response.json()) as ResearchRun;
      router.push(`/zopedia/conversations/${encodeURIComponent(run.conversation_id)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Research could not be started.");
      setSubmitting(false);
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const search = String(new FormData(event.currentTarget).get("q") || "");
    navigate({ q: search.trim(), tab: search.trim() ? "search" : "knowledge", folder: "", page: "", offset: 0 });
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
      await load(searchQuery, folder, offset);
      setTab("sources");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The source could not be stored.");
    } finally { setUploading(false); }
  }

  const completed = useMemo(() => threads.filter((thread) => thread.status === "completed").length, [threads]);
  const active = useMemo(() => threads.filter((thread) => ["queued", "running", "cancel_requested"].includes(thread.status)).length, [threads]);
  const internalLinks = useMemo(() => Object.fromEntries((page?.links || []).flatMap((link) => [link.page_id, link.slug, link.title].filter(Boolean).map((key) => [key.toLowerCase(), `/zopedia?tab=knowledge&page=${encodeURIComponent(link.page_id)}`]))), [page]);
  const filteredThreads = threads.filter((thread) => !searchQuery || thread.title.toLowerCase().includes(searchQuery.toLowerCase()));

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;

  return (
    <div className="page-shell workbook-page zopedia-workspace">
      <header className="artifact-header workbook-heading">
        <div><p className="breadcrumb">Research <span>/</span> Knowledge workspace</p><h1>Zopedia</h1></div>
        <form className="zopedia-search" key={searchQuery} onSubmit={submitSearch}>
          <input aria-label="Search Zopedia" defaultValue={searchQuery} maxLength={300} name="q" placeholder="Search knowledge and sources" type="search" />
          <button className="quiet-button" type="submit">Search</button>
        </form>
      </header>

      <WorkbookTabs active={tab} items={[
        { key: "chat", label: "Research" }, { key: "threads", label: "Conversations" },
        { key: "knowledge", label: "Knowledge" }, { key: "sources", label: "Sources" },
        ...(searchQuery ? [{ key: "search", label: "Search results" }] : []),
      ]} label="Zopedia sections" onChange={setTab} />
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {workspaceError ? <div role="alert" className="subtle-error">{workspaceError} <button className="text-button" onClick={() => void load(searchQuery, folder, offset)} type="button">Retry</button></div> : null}
      {notice ? <p className="admin-notice" role="status">{notice}</p> : null}

      {tab === "search" ? <section className="zopedia-results" aria-busy={loading}>
        <header className="panel-header"><h2>Results for “{searchQuery}”</h2><button type="button" className="text-button" onClick={() => navigate({ q: "", tab: "knowledge", offset: 0 })}>Clear</button></header>
        {loading ? <p className="loading-label">Searching…</p> : workspace ? <>
          <h3>Knowledge pages · {workspace?.counts.pages || 0}</h3>
          <div className="knowledge-search-results">{workspace?.pages.map((item) => <button key={item.page_id} type="button" onClick={() => navigate({ tab: "knowledge", page: item.page_id })}><span>{item.page_type.replaceAll("_", " ")}</span><strong>{item.title}</strong><p>{item.summary}</p></button>)}</div>
          <h3>Sources · {(workspace?.documents.length || 0) + (workspace?.source_pages.length || 0)}</h3>
          <div className="thread-list">{workspace?.source_pages.map((item) => <Link href={`/zopedia?tab=knowledge&page=${encodeURIComponent(item.page_id)}`} key={item.page_id}><span>{item.title}</span><small>{item.summary}</small></Link>)}{workspace?.documents.map((item) => <a href={item.url || undefined} key={item.document_id} rel="noreferrer noopener" target="_blank"><span>{item.title}</span><small>{item.excerpt}</small></a>)}</div>
          {filteredThreads.length ? <><h3>Conversations · {filteredThreads.length}</h3><div className="thread-list">{filteredThreads.map((thread) => <Link key={thread.conversation_id} href={`/zopedia/conversations/${encodeURIComponent(thread.conversation_id)}`}>{thread.title}</Link>)}</div></> : null}
          {!workspace?.pages.length && !workspace?.documents.length && !workspace?.source_pages.length && !filteredThreads.length ? <p className="search-empty">No results for “{searchQuery}”.</p> : null}
          {workspace?.has_more ? <button className="quiet-button" onClick={() => navigate({ offset: offset + 60 })} type="button">More pages</button> : null}
        </> : null}
      </section> : null}

      {tab === "knowledge" ? <section className={`wiki-browser${pageId ? " has-reader" : ""}`}>
        <aside className="wiki-folders" aria-label="Knowledge folders"><button className={!folder ? "active" : ""} onClick={() => navigate({ folder: "", q: "", page: "", offset: 0 })} type="button"><Icon name="book" />All knowledge</button>{folder ? <button onClick={() => navigate({ folder: folder.split("/").slice(0, -1).join("/") === "wiki" ? "" : folder.split("/").slice(0, -1).join("/"), page: "", offset: 0 })} type="button">← Parent folder</button> : null}{workspace?.folders.filter((item) => item.path !== "wiki" && !item.path.startsWith("wiki/sources")).map((item) => <button key={item.folder_id} title={item.path} onClick={() => navigate({ folder: item.path, q: "", page: "", offset: 0 })} type="button"><Icon name="book" /><span>{item.name}</span></button>)}</aside>
        <div className="wiki-files" aria-busy={loading}><header><strong>{folder ? workspace?.folder_name || folder : "Knowledge"}</strong>{workspace ? <span>{workspace.counts.pages} pages</span> : null}</header>
          {loading ? <p className="loading-label">Loading pages…</p> : workspace?.pages.map((item) => <button className={pageId === item.page_id ? "active" : ""} key={item.page_id} onClick={() => navigate({ page: item.page_id })} type="button"><Icon name="book" /><span><strong>{item.title}</strong><small>{item.page_type.replaceAll("_", " ")} · {dateTime(item.updated_at)}</small>{!pageId && item.summary ? <p>{item.summary}</p> : null}</span></button>)}
          {!loading && workspace && !workspace.pages.length ? <p className="search-empty">No pages in this folder.</p> : null}
          <footer>{offset > 0 ? <button className="text-button" onClick={() => navigate({ offset: Math.max(0, offset - 60) })} type="button">Previous</button> : null}{workspace?.has_more ? <button className="text-button" onClick={() => navigate({ offset: offset + 60 })} type="button">Next</button> : null}</footer>
        </div>
        {pageId ? <article className="wiki-reader" ref={reader} aria-busy={pageLoading}>{pageLoading ? <p className="loading-label">Opening page…</p> : pageError ? <p role="alert" className="subtle-error">{pageError}</p> : page ? <>
          <header><div><p className="section-label">{page.page_type.replaceAll("_", " ")}</p><h2>{page.title}</h2><small>v{page.version} · {dateTime(page.updated_at)}</small></div><button className="text-button" onClick={() => navigate({ page: "" })} type="button">Close</button></header>
          <Markdown internalLinks={internalLinks}>{page.body_markdown || page.summary}</Markdown>
          {page.links.length ? <section className="wiki-links"><h3>Connected pages</h3>{page.links.map((link) => <button type="button" key={link.page_id} onClick={() => navigate({ page: link.page_id })}><span>{link.direction === "backlink" ? "Linked from" : "Links to"}</span><strong>{link.title}</strong></button>)}</section> : null}
          {page.source_urls.length ? <section className="wiki-links"><h3>Sources</h3>{page.source_urls.map((url) => <a key={url} href={url} rel="noreferrer noopener" target="_blank">{url}</a>)}</section> : null}
        </> : null}</article> : null}
      </section> : null}

      {tab === "chat" ? <>
        <MetricStrip items={[
          { label: "Conversations", value: threads.length }, { label: "Complete", value: completed }, { label: "Active", value: active },
          { label: "Knowledge pages", value: workspace?.counts.pages ?? "—" }, { label: "Retained sources", value: workspace?.counts.documents ?? "—" },
        ]} />
        <section className="zopedia-research-grid">
          <div className="research-composer conversation-opening">
            <form onSubmit={submitResearch}>
              <label htmlFor="research-query">What do you want to understand?</label>
              <textarea autoFocus id="research-query" maxLength={4000} minLength={1} onChange={(event) => setQuery(event.target.value)} placeholder="Ask Zopedia" required rows={8} value={query} />
              <div className="composer-actions"><button className="primary-button" disabled={submitting} type="submit">{submitting ? "Starting…" : "Start research"}</button></div>
            </form>
          </div>
          <section className="workbook-panel recent-threads">
            <header className="panel-header"><div><h2>Recent conversations</h2></div><button className="text-button" onClick={() => setTab("threads")} type="button">View all</button></header>
            <div className="thread-list">{threads.slice(0, 8).map((thread) => <Link href={`/zopedia/conversations/${encodeURIComponent(thread.conversation_id)}`} key={thread.conversation_id}><span>{thread.title}</span><small>{statusLabel(thread.status)} · {dateTime(thread.updated_at)}</small></Link>)}</div>
          </section>
        </section>
      </> : null}

      {tab === "threads" ? <section className="workbook-panel">
        <header className="panel-header"><div><p className="section-label">Conversation history</p><h2>Conversations</h2></div><span>{threads.length}</span></header>
        <div className="thread-ledger">{threads.map((thread) => <Link href={`/zopedia/conversations/${encodeURIComponent(thread.conversation_id)}`} key={thread.conversation_id}><div><strong>{thread.title}</strong><small>{thread.turn_count} {thread.turn_count === 1 ? "message" : "messages"} · {dateTime(thread.created_at)}</small></div><span className={`status-badge status-${thread.status}`}>{statusLabel(thread.status)}</span></Link>)}</div>
      </section> : null}

      {tab === "sources" ? <>
        <form className="workbook-panel source-upload" onSubmit={upload}>
          <header><p className="section-label">Add evidence</p><h2>Upload a source</h2></header>
          <input accept=".txt,.md,.markdown,.csv,.json,.pdf,text/plain,text/markdown,text/csv,application/json,application/pdf" name="source" required type="file" />
          <button className="primary-button" disabled={uploading} type="submit">{uploading ? "Storing…" : "Store source"}</button>
        </form>
        <section className="knowledge-grid">{workspace?.source_pages.map((page) => <article className="knowledge-card" key={page.page_id}><div><span>{page.page_type.replaceAll("_", " ")}</span><small>v{page.version}</small></div><h2><Link href={`/zopedia?tab=knowledge&page=${encodeURIComponent(page.page_id)}`}>{page.title}</Link></h2>{page.summary ? <p>{page.summary}</p> : null}<footer><time>{dateTime(page.updated_at)}</time>{page.source_urls[0] ? <a href={page.source_urls[0]} rel="noreferrer noopener" target="_blank">Source</a> : null}</footer></article>)}</section>
        <section className="memory-ledger">{workspace?.documents.map((document) => <article className="memory-row" key={document.document_id}><div className="memory-meta"><span>{document.source_kind || "source"}</span><small>{document.provider || document.authority}</small></div><div><h2>{document.url ? <a href={document.url} rel="noreferrer noopener" target="_blank">{document.title}</a> : document.title}</h2>{document.excerpt ? <p>{document.excerpt}</p> : null}<footer>{dateTime(document.published_at || document.as_of)}{document.tickers.length ? ` · ${document.tickers.join(", ")}` : ""}{document.event_tags.length ? ` · ${document.event_tags.slice(0, 3).join(", ")}` : ""}</footer></div></article>)}</section>
        {session.context?.role === "admin" && workspace?.maintenance_reports.length ? <section className="workbook-panel zopedia-health"><header className="panel-header"><div><p className="section-label">Knowledge integrity</p><h2>Maintenance</h2></div><span>{workspace.proposals.length} open proposals</span></header><div className="health-run-list">{workspace.maintenance_reports.map((report, index) => <div key={String(report.run_id || index)}><strong>{String(report.status || "unknown")}</strong><span>{String(report.page_count ?? 0)} pages · {String(report.edge_count ?? 0)} links · {String(report.issue_count ?? 0)} issues</span><time>{dateTime(String(report.created_at_utc || ""))}</time></div>)}</div></section> : null}
      </> : null}
    </div>
  );
}

export default function ZopediaPage() { return <Suspense fallback={null}><ZopediaWorkspace /></Suspense>; }
