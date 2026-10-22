"use client";

import { useEffect, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { MetricStrip } from "@/components/workbook";
import { responseError } from "@/lib/client/api";
import styles from "./trading-paper-portfolio.module.css";

type Observation = { date: string; return_pct: number | null; benchmark_return_pct: number | null };
export type TrackedPaperCall = {
  tracking_id: string; ticker: string; stance: string; horizon_label: string;
  approved_at_utc: string; as_of?: string; status: string; return_pct?: number;
  benchmark_return_pct?: number; excess_return_pct?: number; max_drawdown_pct?: number;
  entry_date?: string; last_price_date?: string; target_date?: string; hypothesis?: string;
  path?: Observation[]; path_status?: string;
};
type Holding = {
  ticker: string; stance: string; tracking_ids: string[]; long_calls: number; short_calls: number;
  contribution_pct: number | null; net_exposure_pct: number | null; gross_exposure_pct: number | null;
  last_price_date: string; unpriced_calls: number;
};
export type PaperPortfolio = {
  status: string; start_date: string; last_price_date: string;
  return_pct: number | null; excess_return_pct: number | null; max_drawdown_pct: number | null;
  path: Observation[]; approved_calls: number; open_calls: number; completed_calls: number;
  pending_calls: number; unpriced_calls: number; current_marks: number; stale_sessions: number; benchmark_status: string;
  allocation: { long_pct: number | null; short_pct: number | null; net_pct: number | null; cash_pct: number | null };
  holdings: Holding[]; attribution: Array<{ ticker: string; contribution_pct: number; calls: number }>;
};
const teal = "#52d6c7", coral = "#f08c73", violet = "#b89cff";
function pct(value: number | null | undefined) { return value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`; }
function pp(value: number | null) { return value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)} pp`; }
function day(value: string) { return value ? new Date(`${value.slice(0, 10)}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "—"; }

export function TradingPaperPortfolio({ portfolio, calls, unavailable, onReviewCalls }: {
  portfolio?: PaperPortfolio; calls: TrackedPaperCall[]; unavailable: boolean; onReviewCalls: () => void;
}) {
  const [selected, setSelected] = useState("");
  const chosen = calls.find(call => call.tracking_id === selected);
  if (chosen) return <section className={styles.detail}>
    <button className="quiet-button" type="button" onClick={() => setSelected("")}>← Paper portfolio</button>
    <ApprovedCallDetail call={chosen} />
  </section>;
  if (unavailable || !portfolio) return <p className="subtle-error" role="alert">Portfolio data is unavailable.</p>;
  if (portfolio.status === "empty") return <section className={`workbook-panel ${styles.empty}`}>
    <h2>Paper portfolio</h2><p>Approve a call to start tracking your portfolio.</p>
    <button className="primary-button" type="button" onClick={onReviewCalls}>Review calls</button>
  </section>;
  const allocation = portfolio.allocation;
  const attribution = [...portfolio.attribution].sort((a, b) => Math.abs(b.contribution_pct) - Math.abs(a.contribution_pct)).slice(0, 8).reverse();
  const closed = calls.filter(call => call.status === "matured");
  const pending = calls.filter(call => call.status === "pending_entry");
  function callButtons(items: TrackedPaperCall[]) {
    return <div className={styles.calls}>{items.map(call => <button key={call.tracking_id} type="button" onClick={() => setSelected(call.tracking_id)}>
      <span><strong>{call.ticker}</strong> · {call.stance} · {call.horizon_label}<small>Approved {day(call.approved_at_utc)} · {call.entry_date ? `Entry ${day(call.entry_date)}` : "Awaiting entry"}</small></span>
      <span>{pct(call.return_pct)} <span aria-hidden="true">›</span></span>
    </button>)}</div>;
  }
  return <div className={styles.portfolio}>
    <section className="workbook-panel">
      <header className="panel-header"><h2>Paper portfolio</h2>{portfolio.last_price_date ? <time dateTime={portfolio.last_price_date}>Prices through {day(portfolio.last_price_date)}</time> : null}</header>
      <MetricStrip items={[
        {label: portfolio.unpriced_calls ? "Priced-call return" : "Portfolio return", value: pct(portfolio.return_pct)},
        {label: "Vs matched SPY", value: pct(portfolio.excess_return_pct)},
        {label: "Max drawdown", value: pct(portfolio.max_drawdown_pct)},
        {label: "Open calls", value: portfolio.open_calls},
      ]} />
      {portfolio.status === "unavailable" ? <p className="subtle-error" role="alert">Portfolio performance is unavailable.</p> : null}
      {portfolio.status === "pending" ? <p>Entry at the next trading day’s close.</p> : null}
      {portfolio.status === "capital_exhausted" ? <p role="alert">Portfolio capital was exhausted; subsequent returns are unavailable.</p> : null}
      {portfolio.path.length ? <div className={styles.chart}><DataPlot displayModeBar={false} id="paper-portfolio-return" title="Portfolio return" data={[
        {type: "scatter", mode: portfolio.path.length === 1 ? "markers" : "lines", name: "Paper portfolio", x: portfolio.path.map(p => p.date), y: portfolio.path.map(p => p.return_pct), connectgaps: false, line: {color: teal, width: 2.4}},
        ...(portfolio.benchmark_status === "available" ? [{type: "scatter", mode: "lines", name: "Matched SPY", x: portfolio.path.map(p => p.date), y: portfolio.path.map(p => p.benchmark_return_pct), line: {color: violet, dash: "dot"}}] : []),
      ]} layout={{xaxis: {type: "date"}, yaxis: {title: {text: "Return (%)"}, zeroline: true, ticksuffix: "%"}, showlegend: true, hovermode: "x unified", height: 330}} rowCount={portfolio.path.length} /></div> : null}
      <p className={styles.method}>Equal entry capital · time-weighted returns · cash after exit. SPY follows the same dates and long/short mix.</p>
      {portfolio.status === "partial" ? <p className={styles.coverage}>{portfolio.current_marks} of {portfolio.open_calls} open calls have current marks{portfolio.unpriced_calls ? ` · ${portfolio.unpriced_calls} unpriced` : ""}{portfolio.stale_sessions ? ` · ${portfolio.stale_sessions} sessions use older marks` : ""}</p> : null}
    </section>
    {portfolio.path.length ? <div className={styles.analytics}>
      <section className="workbook-panel"><header className="panel-header"><h2>{portfolio.unpriced_calls ? "Priced exposure" : "Exposure"}</h2></header>
        <MetricStrip items={[{label: "Net", value: pct(allocation.net_pct)}, {label: "Closed-call cash", value: pct(allocation.cash_pct)}]} />
        <div className={styles.chart} style={{height: 190}}><DataPlot displayModeBar={false} id="paper-portfolio-exposure" title="" data={[
          {type: "bar", orientation: "h", name: "Long", y: ["Long"], x: [allocation.long_pct], marker: {color: teal}},
          {type: "bar", orientation: "h", name: "Short", y: ["Short"], x: [allocation.short_pct == null ? null : -allocation.short_pct], marker: {color: coral}},
        ]} layout={{height: 190, xaxis: {title: {text: "Exposure (% of portfolio value)"}, zeroline: true, ticksuffix: "%"}, showlegend: false, margin: {l: 50, r: 20, t: 12, b: 42}}} rowCount={2} /></div>
      </section>
      <section className="workbook-panel"><header className="panel-header"><h2>Return contributors</h2></header>
        <div className={styles.chart} style={{height: Math.max(220, attribution.length * 28 + 65)}}><DataPlot displayModeBar={false} id="paper-portfolio-contribution" title="" data={[{type: "bar", orientation: "h", y: attribution.map(p => p.ticker), x: attribution.map(p => p.contribution_pct), marker: {color: attribution.map(p => p.contribution_pct >= 0 ? teal : coral)}, hovertemplate: "%{y}: %{x:.2f} pp<extra></extra>"}]} layout={{height: Math.max(220, attribution.length * 28 + 65), xaxis: {title: {text: "Contribution (percentage points)"}, zeroline: true, ticksuffix: " pp"}, yaxis: {type: "category", tickmode: "array", tickvals: attribution.map(p => p.ticker), ticktext: attribution.map(p => p.ticker), automargin: true}, margin: {l: 60, r: 20, t: 12, b: 42}}} rowCount={attribution.length} /></div>
      </section>
    </div> : null}
    {portfolio.holdings.length ? <section className={`workbook-panel ${styles.holdings}`}>
      <header className="panel-header"><h2>Positions</h2><span>{portfolio.holdings.length} symbols</span></header>
      <div className={styles.columns}><span>Symbol / calls</span><span>Net exposure</span><span>Contribution</span></div>
      {portfolio.holdings.map(holding => <details className={styles.holding} key={holding.ticker}>
        <summary><span><strong>{holding.ticker}</strong><small>{[holding.long_calls ? `${holding.long_calls} long` : "", holding.short_calls ? `${holding.short_calls} short` : ""].filter(Boolean).join(" · ")}</small></span>
          <span>{pct(holding.net_exposure_pct)}{holding.stance === "mixed" ? <small>Gross {pct(holding.gross_exposure_pct)}</small> : null}</span>
          <span style={{color: holding.contribution_pct == null ? undefined : holding.contribution_pct < 0 ? coral : teal}}>{pp(holding.contribution_pct)}<small aria-hidden="true">View calls ›</small></span>
        </summary>
        {holding.unpriced_calls ? <p className={styles.coverage}>{holding.unpriced_calls} calls awaiting prices</p> : null}
        {callButtons(calls.filter(call => holding.tracking_ids.includes(call.tracking_id)))}
      </details>)}
    </section> : null}
    {pending.length ? <details className="workbook-panel"><summary>Pending entry · {pending.length}</summary>{callButtons(pending)}</details> : null}
    {closed.length ? <details className="workbook-panel"><summary>Closed calls · {closed.length}</summary>{callButtons(closed)}</details> : null}
  </div>;
}

function ApprovedCallDetail({call}: {call: TrackedPaperCall}) {
  const [detail, setDetail] = useState<TrackedPaperCall | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`/api/trading-agent/approved-calls/${encodeURIComponent(call.tracking_id)}`, {cache: "no-store", credentials: "same-origin", signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error(await responseError(response, "Call tracking is unavailable."));
      const next = await response.json() as TrackedPaperCall;
      if (!controller.signal.aborted) { setDetail(next); setError(""); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Call tracking is unavailable."); });
    return () => controller.abort();
  }, [call.tracking_id, call.as_of]);
  const current = detail?.tracking_id === call.tracking_id ? detail : call;
  const path = current.path || [];
  const benchmark = current.stance === "short" ? "SPY short" : "SPY long";
  return <section className="workbook-panel">
    <header className="panel-header"><h2>{current.ticker} · {current.stance} · {current.horizon_label}</h2></header>
    {error ? <p className="subtle-error" role="alert">{error}</p> : null}
    <MetricStrip items={[{label: "Return", value: pct(current.return_pct)}, {label: `Vs ${benchmark}`, value: pct(current.excess_return_pct)}, {label: "Drawdown", value: pct(current.max_drawdown_pct)}]} />
    {current.status === "pending_entry" ? <p>Entry at the next trading day’s close.</p> : null}
    {current.status === "price_unavailable" ? <p>Price data is unavailable.</p> : null}
    {current.path_status === "unavailable" ? <p>Price history is unavailable.</p> : null}
    {path.length ? <DataPlot id={`tracked-${current.tracking_id}`} title={`${current.ticker} · paper return`} data={[
      {type: "scatter", mode: path.length === 1 ? "markers" : "lines", name: "Call", x: path.map(p => p.date), y: path.map(p => p.return_pct), line: {color: teal}},
      {type: "scatter", mode: "lines", name: benchmark, x: path.map(p => p.date), y: path.map(p => p.benchmark_return_pct), connectgaps: false, line: {color: violet, dash: "dot"}},
    ]} layout={{xaxis: {type: "date"}, yaxis: {title: {text: "Return (%)"}, zeroline: true, ticksuffix: "%"}, showlegend: true}} rowCount={path.length} /> : null}
    {current.entry_date ? <p className={styles.method}>Entry {day(current.entry_date)} · Target {day(current.target_date || "")} · Prices through {day(current.last_price_date || "")}</p> : null}
    {current.hypothesis ? <p>{current.hypothesis}</p> : null}
  </section>;
}
