import Link from "next/link";
import Image from "next/image";
import { Markdown } from "@/components/markdown";
import { Visualization } from "@/components/visualizations/visualization";
import { backendFetch } from "@/lib/server/backend";
import type { HomeTickerSnapshot, HomeView } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadHome(targetDate: string): Promise<HomeView | null> {
  try {
    const path = `/v1/home${targetDate ? `?date=${encodeURIComponent(targetDate)}` : ""}`;
    const response = await backendFetch(path, {
      timeoutMs: 20_000,
      cache: "force-cache",
      next: { revalidate: targetDate ? 3_600 : 30 },
    });
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

function numeric(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatPrice(value: number | null) {
  const parsed = numeric(value);
  if (parsed === null) return "—";
  return parsed.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: parsed >= 1_000 ? 0 : 2 });
}

function formatFinancial(value: number | null) {
  const parsed = numeric(value);
  if (parsed === null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(parsed);
}

function formatPercent(value: number | null) {
  const parsed = numeric(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(1)}%`;
}

function changeTone(value: number | null) {
  const parsed = numeric(value);
  if (parsed === null || parsed === 0) return "flat";
  return parsed > 0 ? "positive" : "negative";
}

function TickerSnapshot({ ticker }: { ticker: HomeTickerSnapshot }) {
  const company = ticker.company_name && ticker.company_name !== ticker.symbol ? ticker.company_name : "";
  return (
    <Link className="attention-ticker" href={`/stock-investigator?ticker=${encodeURIComponent(ticker.symbol)}`}>
      <header>
        <div><strong>{ticker.symbol}</strong>{company ? <span>{company}</span> : null}</div>
        <div className="ticker-price">
          <strong>{formatPrice(ticker.latest_close)}</strong>
          <span className={changeTone(ticker.price_change_1d_pct)}>{formatPercent(ticker.price_change_1d_pct)}</span>
        </div>
      </header>
      {ticker.sparkline_data_uri ? <Image alt="" aria-hidden="true" height={56} src={ticker.sparkline_data_uri} unoptimized width={164} /> : null}
      <dl>
        <div><dt>Market cap</dt><dd>{ticker.market_cap_label && ticker.market_cap_label !== "n/a" ? ticker.market_cap_label : formatFinancial(ticker.market_cap)}</dd></div>
        <div><dt>Revenue</dt><dd>{formatFinancial(ticker.revenue)}</dd></div>
        <div><dt>Revenue YoY</dt><dd className={changeTone(ticker.revenue_yoy_pct)}>{formatPercent(ticker.revenue_yoy_pct)}</dd></div>
        <div><dt>Net income</dt><dd>{formatFinancial(ticker.net_income)}</dd></div>
        <div><dt>Free cash flow</dt><dd>{formatFinancial(ticker.free_cash_flow)}</dd></div>
        <div><dt>1 month</dt><dd className={changeTone(ticker.price_change_1m_pct)}>{formatPercent(ticker.price_change_1m_pct)}</dd></div>
      </dl>
    </Link>
  );
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
                      {beat.symbols.length ? <span>{beat.symbols.length} {beat.symbols.length === 1 ? "company" : "companies"}</span> : null}
                    </div>
                    <h3>{beat.title}</h3>
                    {beat.summary ? <p>{beat.summary}</p> : null}
                    {beat.business_context ? <p className="business-context">{beat.business_context}</p> : null}
                    {beat.tickers.length ? <div className="attention-ticker-grid">{beat.tickers.map((ticker) => <TickerSnapshot key={ticker.symbol} ticker={ticker} />)}</div> : null}
                  </article>
                </li>
              ))}
            </ol>
          </section>

          {home.graph ? (
            <section className="home-graph-panel" aria-label="Market attention network">
              <header className="section-heading">
                <div><p className="section-label">Structure</p><h2>{home.graph.title}</h2></div>
                <span>{home.graph.performance.row_count.toLocaleString()} plotted points</span>
              </header>
              <Visualization artifact={home.graph} />
            </section>
          ) : null}
        </>
      )}
    </div>
  );
}
