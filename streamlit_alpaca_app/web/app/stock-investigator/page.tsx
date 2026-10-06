"use client";

import type { FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { Visualization } from "@/components/visualizations/visualization";
import { responseError, secureMutation } from "@/lib/client/api";
import type {
  DatasetArtifact,
  Session,
  StockInvestigatorView,
  VisualizationArtifact,
  VisualizationResponse,
} from "@/lib/types";

type InvestigatorView = "chart" | "data" | "provenance";
type ArtifactKey = "price" | "pullback" | "income" | "balance" | "cashflow";

const artifactDefinitions: Record<ArtifactKey, { label: string; endpoint: string; statement?: string }> = {
  price: { label: "Price channel", endpoint: "technical_price_channel" },
  pullback: { label: "Pullback", endpoint: "technical_pullback" },
  income: { label: "Income", endpoint: "fundamental_statement", statement: "income" },
  balance: { label: "Balance", endpoint: "fundamental_statement", statement: "balance" },
  cashflow: { label: "Cash flow", endpoint: "fundamental_statement", statement: "cashflow" },
};

function normalizedTicker(value: string | null) {
  const ticker = String(value || "AAPL").trim().toUpperCase();
  return /^[A-Z0-9.-]{1,12}$/.test(ticker) ? ticker : "AAPL";
}

function normalizedDays(value: string | null) {
  const parsed = Number(value || 365);
  if (!Number.isFinite(parsed)) return 365;
  const bounded = Math.min(720, Math.max(60, Math.round(parsed)));
  return [60, 180, 365, 720].reduce((closest, candidate) => (
    Math.abs(candidate - bounded) < Math.abs(closest - bounded) ? candidate : closest
  ), 365);
}

function numberValue(record: Record<string, unknown> | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value: number | null) {
  return value === null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}

function percent(value: number | null, fraction = false) {
  if (value === null) return "—";
  const resolved = fraction ? value * 100 : value;
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(resolved)}%`;
}

function numberFrom(record: Record<string, unknown> | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function shortDate(value: string) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }).format(value);
  return String(value);
}

function DatasetTable({ dataset }: { dataset: DatasetArtifact }) {
  const rows = dataset.records.slice(-120).reverse();
  return (
    <div className="dataset-frame">
      <table>
        <thead><tr>{dataset.columns.map((column) => <th key={column.name}>{column.name.replaceAll("_", " ")}</th>)}</tr></thead>
        <tbody>
          {rows.map((record, rowIndex) => (
            <tr key={`${dataset.dataset_id}-${rowIndex}`}>
              {dataset.columns.map((column) => <td key={column.name}>{formatCell(record[column.name])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StockInvestigatorWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialTicker = normalizedTicker(searchParams.get("ticker"));
  const initialDays = normalizedDays(searchParams.get("days"));
  const [session, setSession] = useState<Session | null>(null);
  const [ticker, setTicker] = useState(initialTicker);
  const [days, setDays] = useState(initialDays);
  const [artifacts, setArtifacts] = useState<Partial<Record<ArtifactKey, VisualizationArtifact>>>({});
  const [snapshot, setSnapshot] = useState<StockInvestigatorView | null>(null);
  const [activeArtifact, setActiveArtifact] = useState<ArtifactKey>("price");
  const [view, setView] = useState<InvestigatorView>("chart");
  const [error, setError] = useState("");
  const [snapshotError, setSnapshotError] = useState("");
  const [loading, setLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const initialLoad = useRef(false);

  const artifact = artifacts[activeArtifact] || null;
  const dataset = useMemo(() => artifact ? Object.values(artifact.datasets)[0] || null : null, [artifact]);
  const priceDataset = useMemo(() => artifacts.price ? Object.values(artifacts.price.datasets)[0] || null : null, [artifacts.price]);
  const latest = priceDataset?.records.at(-1);
  const close = numberFrom(snapshot?.technical, "close") ?? numberValue(latest, "close");
  const support = numberFrom(snapshot?.technical, "channel_support") ?? numberValue(latest, "channel_support");
  const resistance = numberFrom(snapshot?.technical, "channel_resistance") ?? numberValue(latest, "channel_resistance");
  const ath = numberFrom(snapshot?.technical, "ath") ?? numberValue(latest, "ath");
  const hasMarketMetrics = [close, support, resistance, ath].some((value) => value !== null);
  const company = snapshot?.company;
  const forecast = snapshot?.forecast;
  const hasCompanyBrief = Boolean(company && (company.headline || company.summary || company.why_now || company.management_signal || company.background));

  const fetchArtifact = useCallback(async (key: ArtifactKey, requestedTicker: string, requestedDays: number) => {
    const definition = artifactDefinitions[key];
    const params = definition.statement
      ? { ticker: requestedTicker, statement: definition.statement }
      : { ticker: requestedTicker, days: requestedDays };
    const response = await secureMutation(`/api/visualizations/${definition.endpoint}`, {
      method: "POST",
      body: JSON.stringify({ params }),
    });
    if (!response.ok) throw new Error(await responseError(response, `${definition.label} could not be loaded.`));
    const payload = (await response.json()) as VisualizationResponse;
    if (payload.result_type !== "visualization" || !payload.payload) throw new Error("Visualization response is invalid.");
    return payload.payload;
  }, []);

  const loadTechnicals = useCallback(async (requestedTicker: string, requestedDays: number) => {
    setLoading(true);
    setError("");
    const [priceResult, pullbackResult] = await Promise.allSettled([
      fetchArtifact("price", requestedTicker, requestedDays),
      fetchArtifact("pullback", requestedTicker, requestedDays),
    ]);
    const next: Partial<Record<ArtifactKey, VisualizationArtifact>> = {};
    const failures: string[] = [];
    if (priceResult.status === "fulfilled") next.price = priceResult.value;
    else failures.push(priceResult.reason instanceof Error ? priceResult.reason.message : "Price channel could not be loaded.");
    if (pullbackResult.status === "fulfilled") next.pullback = pullbackResult.value;
    else failures.push(pullbackResult.reason instanceof Error ? pullbackResult.reason.message : "Pullback could not be loaded.");
    setArtifacts(next);
    setActiveArtifact(next.price ? "price" : "pullback");
    setView("chart");
    setError([...new Set(failures)].join(" "));
    setLoading(false);
  }, [fetchArtifact]);

  const loadSnapshot = useCallback(async (requestedTicker: string, requestedDays: number) => {
    setSnapshotLoading(true);
    setSnapshotError("");
    try {
      const response = await fetch(
        `/api/investigator/${encodeURIComponent(requestedTicker)}?days=${requestedDays}`,
        { cache: "no-store", credentials: "same-origin" },
      );
      if (!response.ok) throw new Error(await responseError(response, "Company context could not be loaded."));
      const next = (await response.json()) as StockInvestigatorView;
      setSnapshot(next.ticker === requestedTicker ? next : null);
    } catch (reason) {
      setSnapshot(null);
      setSnapshotError(reason instanceof Error ? reason.message : "Company context could not be loaded.");
    } finally {
      setSnapshotLoading(false);
    }
  }, []);

  useEffect(() => {
    fetch("/api/auth/session", { cache: "no-store" })
      .then(async (response) => response.json())
      .then((payload: Session) => {
        setSession(payload);
        if (!payload.authenticated) router.replace("/login?next=/stock-investigator");
      })
      .catch(() => setError("Session could not be verified."));
  }, [router]);

  useEffect(() => {
    if (!session?.authenticated || initialLoad.current) return;
    initialLoad.current = true;
    void Promise.all([loadTechnicals(initialTicker, initialDays), loadSnapshot(initialTicker, initialDays)]);
  }, [initialDays, initialTicker, loadSnapshot, loadTechnicals, session]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const requestedTicker = normalizedTicker(ticker);
    const requestedDays = normalizedDays(String(days));
    setTicker(requestedTicker);
    setDays(requestedDays);
    setSnapshot(null);
    router.replace(`/stock-investigator?ticker=${encodeURIComponent(requestedTicker)}&days=${requestedDays}`);
    void Promise.all([loadTechnicals(requestedTicker, requestedDays), loadSnapshot(requestedTicker, requestedDays)]);
  }

  async function selectArtifact(key: ArtifactKey) {
    setActiveArtifact(key);
    setView("chart");
    setError("");
    if (artifacts[key]) return;
    setLoading(true);
    try {
      const next = await fetchArtifact(key, ticker, days);
      setArtifacts((current) => ({ ...current, [key]: next }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${artifactDefinitions[key].label} could not be loaded.`);
    } finally {
      setLoading(false);
    }
  }

  if (!session?.authenticated) {
    return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  }

  return (
    <div className="page-shell investigator-page">
      <header className="artifact-header investigator-heading">
        <div>
          <p className="breadcrumb">Research <span>/</span> Equity</p>
          <div className="entity-title">
            <h1>{ticker}</h1>
            <span>{company?.name || company?.business_lens || artifactDefinitions[activeArtifact].label}</span>
          </div>
        </div>
        <form className="investigator-controls" onSubmit={submit}>
          <label>
            Symbol
            <input
              autoCapitalize="characters"
              maxLength={12}
              name="ticker"
              onChange={(event) => setTicker(event.target.value.toUpperCase())}
              pattern="[A-Za-z0-9.-]{1,12}"
              required
              value={ticker}
            />
          </label>
          <label>
            Range
            <select name="days" onChange={(event) => setDays(Number(event.target.value))} value={days}>
              <option value={60}>60D</option>
              <option value={180}>6M</option>
              <option value={365}>1Y</option>
              <option value={720}>2Y</option>
            </select>
          </label>
          <button className="primary-button" disabled={loading} type="submit">{loading ? "Loading…" : "Load"}</button>
        </form>
      </header>

      {hasMarketMetrics ? (
        <dl className="market-metrics" aria-label={`${ticker} latest values`}>
          <div><dt>Close</dt><dd>{money(close)}</dd></div>
          <div><dt>Support</dt><dd>{money(support)}</dd></div>
          <div><dt>Resistance</dt><dd>{money(resistance)}</dd></div>
          <div><dt>All-time high</dt><dd>{money(ath)}</dd></div>
        </dl>
      ) : null}

      {hasCompanyBrief ? (
        <section className="company-brief" aria-busy={snapshotLoading}>
          <div className="company-brief-copy">
            <p className="section-label">Company context</p>
            {company?.headline ? <h2>{company.headline}</h2> : null}
            {company?.summary ? <p className="company-summary">{company.summary}</p> : null}
            {company?.why_now || company?.management_signal ? (
              <dl className="context-notes">
                {company.why_now ? <div><dt>Why now</dt><dd>{company.why_now}</dd></div> : null}
                {company.management_signal ? <div><dt>Management signal</dt><dd>{company.management_signal}</dd></div> : null}
              </dl>
            ) : null}
            {!company?.summary && company?.background ? <p className="company-summary">{company.background}</p> : null}
          </div>
          {forecast && Object.values(forecast).some((value) => typeof value === "number") ? (
            <dl className="forecast-ledger" aria-label="Next-week forecast">
              <div><dt>1W up</dt><dd>{percent(numberFrom(forecast, "up_probability"), true)}</dd></div>
              <div><dt>Breakout</dt><dd>{percent(numberFrom(forecast, "breakout_probability"), true)}</dd></div>
              <div><dt>Expected 5D</dt><dd>{percent(numberFrom(forecast, "expected_return_pct"))}</dd></div>
              <div><dt>Analogs</dt><dd>{numberFrom(forecast, "analog_count")?.toLocaleString() || "—"}</dd></div>
            </dl>
          ) : null}
        </section>
      ) : null}

      {snapshotError ? <p className="subtle-error" role="status">{snapshotError}</p> : null}

      <section className="artifact-panel" aria-busy={loading}>
        <nav className="analysis-tabs" aria-label="Equity analysis artifacts">
          {(Object.keys(artifactDefinitions) as ArtifactKey[]).map((key) => (
            <button
              aria-current={activeArtifact === key ? "page" : undefined}
              className={activeArtifact === key ? "active" : ""}
              disabled={loading && activeArtifact === key && !artifacts[key]}
              key={key}
              onClick={() => void selectArtifact(key)}
              type="button"
            >
              {artifactDefinitions[key].label}
              {artifacts[key] ? <span className="loaded-dot" /> : null}
            </button>
          ))}
        </nav>
        <header className="artifact-toolbar">
          <div className="view-tabs" role="tablist" aria-label="Artifact view">
            <button aria-selected={view === "chart"} className={view === "chart" ? "active" : ""} onClick={() => setView("chart")} role="tab" type="button"><Icon name="chart" />Chart</button>
            <button aria-selected={view === "data"} className={view === "data" ? "active" : ""} disabled={!dataset} onClick={() => setView("data")} role="tab" type="button"><Icon name="table" />Data</button>
            <button aria-selected={view === "provenance"} className={view === "provenance" ? "active" : ""} disabled={!artifact} onClick={() => setView("provenance")} role="tab" type="button">Provenance</button>
          </div>
          {artifact ? <span className="artifact-row-count">{artifact.performance.row_count.toLocaleString()} rows</span> : null}
        </header>

        {error ? <div className="visualization-error" role="alert">{error}</div> : null}
        {artifact && view === "chart" ? <Visualization artifact={artifact} /> : null}
        {dataset && view === "data" ? <DatasetTable dataset={dataset} /> : null}
        {artifact && view === "provenance" ? (
          <dl className="provenance-grid">
            <div><dt>Artifact</dt><dd>{artifact.artifact_id}</dd></div>
            <div><dt>Source mode</dt><dd>{artifact.provenance.mode}</dd></div>
            <div><dt>Datasets</dt><dd>{artifact.provenance.datasets.join(", ")}</dd></div>
            <div><dt>Renderer</dt><dd>{artifact.renderer_contract}</dd></div>
            <div><dt>Delivery</dt><dd>{artifact.performance.delivery}</dd></div>
            <div><dt>Encoded size</dt><dd>{Math.round(artifact.performance.encoded_bytes / 1024).toLocaleString()} KB</dd></div>
          </dl>
        ) : null}
        {loading && !artifact ? <div className="visualization-loading" aria-label="Loading visualization" /> : null}
      </section>

      {snapshot?.articles.length ? (
        <section className="evidence-section" aria-label={`${ticker} recent evidence`}>
          <header className="section-heading">
            <div><p className="section-label">Evidence</p><h2>Recent company signals</h2></div>
            <span>{snapshot.articles.length} sources</span>
          </header>
          <ol className="evidence-list">
            {snapshot.articles.map((article, index) => (
              <li key={`${article.url || article.headline}-${index}`}>
                <span className="signal-index">{String(index + 1).padStart(2, "0")}</span>
                <article>
                  <div className="signal-meta">
                    {article.source ? <span className="signal-kind">{article.source}</span> : null}
                    {article.published_at ? <time dateTime={article.published_at}>{shortDate(article.published_at)}</time> : null}
                  </div>
                  <h3>{article.url ? <a href={article.url} rel="noreferrer" target="_blank">{article.headline}</a> : article.headline}</h3>
                  {article.summary ? <p>{article.summary}</p> : null}
                </article>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

export default function StockInvestigatorPage() {
  return <Suspense fallback={null}><StockInvestigatorWorkspace /></Suspense>;
}
