import Link from "next/link";
import { Markdown } from "@/components/markdown";
import { Visualization } from "@/components/visualizations/visualization";
import { backendFetch } from "@/lib/server/backend";
import type { HomeView } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadHome(targetDate: string): Promise<HomeView | null> {
  try {
    const path = `/v1/home${targetDate ? `?date=${encodeURIComponent(targetDate)}` : ""}`;
    const response = await backendFetch(path, { timeoutMs: 20_000 });
    return response.ok ? (await response.json()) as HomeView : null;
  } catch {
    return null;
  }
}

function normalizedDate(value: string | string[] | undefined) {
  const candidate = typeof value === "string" ? value.trim() : "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(candidate)) return "";
  const parsed = new Date(`${candidate}T00:00:00Z`);
  const today = new Date();
  if (Number.isNaN(parsed.getTime()) || parsed.getTime() > today.getTime()) return "";
  return candidate;
}

function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

function formatAsOf(value: string) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(parsed);
}

function beatKind(kind: string) {
  if (kind === "event") return "Event";
  if (kind === "unresolved") return "Watch";
  return "Mover";
}

export default async function HomePage({ searchParams }: { searchParams: Promise<{ date?: string | string[] }> }) {
  const params = await searchParams;
  const targetDate = normalizedDate(params.date);
  const home = await loadHome(targetDate);
  const available = home?.status === "available";
  return (
    <div className="page-shell home-workspace">
      <header className="artifact-header">
        <div>
          <p className="breadcrumb">Home <span>/</span> Daily Brief</p>
          <h1>Market signal</h1>
        </div>
        <div className="home-header-tools">
          {available && home.as_of ? <time dateTime={home.as_of}>{formatAsOf(home.as_of)}</time> : null}
          <form action="/" className="replay-control" method="get">
            <label htmlFor="home-replay-date">Replay</label>
            <input defaultValue={targetDate} id="home-replay-date" max={todayUtc()} name="date" type="date" />
            <button type="submit">View</button>
          </form>
        </div>
      </header>

      {available && home.is_replay ? (
        <div className="replay-banner">
          <span>Historical snapshot <strong>{home.view_date}</strong></span>
          <Link href="/">Return to latest</Link>
        </div>
      ) : null}

      {!available ? (
        <section className="empty-state" aria-live="polite">
          <strong>Daily Brief unavailable</strong>
          <p>The latest materialized market view could not be loaded.</p>
        </section>
      ) : (
        <>
          {home.summary ? (
            <section className="brief-panel">
              <div className="brief-copy">
                <p className="section-label">Daily brief</p>
                <h2>{home.summary.headline}</h2>
                <Markdown>{home.summary.markdown}</Markdown>
                {home.summary.audio_available ? (
                  <audio
                    aria-label="Daily Brief audio"
                    className="brief-audio"
                    controls
                    preload="none"
                    src={`/api/home/audio${home.is_replay && home.view_date ? `?date=${encodeURIComponent(home.view_date)}` : ""}`}
                  />
                ) : null}
              </div>
              <dl className="coverage-ledger" aria-label="Coverage">
                <div><dt>Events</dt><dd>{home.coverage.event_count || 0}</dd></div>
                <div><dt>Must read</dt><dd>{home.coverage.must_read_count || 0}</dd></div>
                <div><dt>News backed</dt><dd>{home.coverage.news_backed_count || 0}</dd></div>
                <div><dt>Watch</dt><dd>{home.coverage.unresolved_count || 0}</dd></div>
              </dl>
            </section>
          ) : null}

          {home.graph ? (
            <section className="home-graph-panel" aria-label="Market attention network">
              <header className="section-heading">
                <div><p className="section-label">Structure</p><h2>{home.graph.title}</h2></div>
                <span>{home.graph.performance.row_count.toLocaleString()} plotted points</span>
              </header>
              <Visualization artifact={home.graph} />
            </section>
          ) : null}

          <section className="signal-section" aria-label="Market movements">
            <header className="section-heading">
              <div><p className="section-label">Attention</p><h2>Market movements</h2></div>
              <span>{home.beats.length} signals</span>
            </header>
            <ol className="signal-list">
              {home.beats.map((beat, index) => (
                <li className={`signal-row signal-${beat.kind}`} key={beat.id || `${beat.kind}-${index}`}>
                  <span className="signal-index">{String(index + 1).padStart(2, "0")}</span>
                  <article>
                    <div className="signal-meta">
                      <span className="signal-kind">{beatKind(beat.kind)}</span>
                      {beat.symbols.length ? <span>{beat.symbols.join(" · ")}</span> : null}
                    </div>
                    <h3>{beat.title}</h3>
                    {beat.summary ? <p>{beat.summary}</p> : null}
                    {beat.business_context ? <p className="business-context">{beat.business_context}</p> : null}
                    {beat.symbols[0] ? (
                      <Link className="entity-link" href={`/stock-investigator?ticker=${encodeURIComponent(beat.symbols[0])}`}>
                        Open {beat.symbols[0]} <span aria-hidden="true">↗</span>
                      </Link>
                    ) : null}
                  </article>
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </div>
  );
}
