"use client";

import { useEffect, useRef, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { MetricStrip, DataTable } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import styles from "./trading-paper-portfolio.module.css";

type Observation = { date: string; return_pct: number | null; benchmark_return_pct: number | null };
export type TrackedPaperCall = {
  tracking_id: string; ticker: string; stance: string; horizon_label: string;
  approved_at_utc: string; as_of?: string; status: string; return_pct?: number;
  benchmark_return_pct?: number; excess_return_pct?: number; max_drawdown_pct?: number;
  entry_date?: string; last_price_date?: string; target_date?: string; hypothesis?: string;
  path?: Observation[]; path_status?: string;
};
export type PaperPortfolio = {
  status: string; as_of?: string; return_pct?: number; max_drawdown_pct?: number;
  summary?: Record<string, number | string | boolean | null>;
  path?: Array<{date: string; equity: number | null; return_pct: number | null}>;
  holdings?: Array<Record<string, string | number>>;
  orders?: Array<Record<string, string | number>>;
  unavailable?: string[]; coverage?: Record<string, string>;
  risk_policy?: RiskPolicy;
};
const teal = "#52d6c7", violet = "#b89cff";
function pct(value: number | null | undefined) { return value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`; }
function day(value: string) { return value ? new Date(`${value.slice(0, 10)}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }) : "—"; }
function numeric(value: unknown) { return value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value); }
function money(value: unknown) { const n = numeric(value); return n == null ? "—" : n.toLocaleString("en-US", {style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2}); }

export function TradingPaperPortfolio({ portfolio, calls, onReviewCalls, onRefresh, refreshing }: {
  portfolio?: PaperPortfolio; calls: TrackedPaperCall[]; onReviewCalls: () => void;
  onRefresh: () => void; refreshing: boolean;
}) {
  const [selected, setSelected] = useState("");
  const chosen = calls.find(call => call.tracking_id === selected);
  if (chosen) return <section className={styles.detail}>
    <button className="quiet-button" type="button" onClick={() => setSelected("")}>← Paper portfolio</button>
    <ApprovedCallDetail call={chosen} />
  </section>;
  const available = portfolio && ["available", "partial"].includes(portfolio.status);
  const summary = portfolio?.summary || {};
  const path = portfolio?.path || [];
  const equityValues = path.map(point => point.equity).filter((value): value is number => value != null && Number.isFinite(value));
  const low = Math.min(...equityValues), high = Math.max(...equityValues);
  const equityRange = equityValues.length && high === low ? [low - Math.max(1, Math.abs(low) * .01), high + Math.max(1, Math.abs(high) * .01)] : undefined;
  const holdings = portfolio?.holdings || [];
  return <div className={styles.portfolio}>
    <section className="workbook-panel">
      <header className="panel-header"><div><h2>Alpaca paper portfolio</h2>{portfolio?.as_of ? <time dateTime={portfolio.as_of}>{new Date(portfolio.as_of).toLocaleString()}</time> : null}</div>
        {portfolio?.status !== "unlinked" ? <button className="quiet-button" type="button" disabled={refreshing} onClick={onRefresh}>{refreshing ? "Refreshing…" : "Refresh"}</button> : null}
      </header>
      {!available ? <p role="status">{portfolio?.status === "unlinked" ? "No Alpaca paper account connected." : portfolio?.status === "pending" ? "The paper account is waiting for its first refresh." : "Paper account data is unavailable."}</p> : <>
        <MetricStrip items={[{label: "Equity", value: money(summary.equity)}, {label: "Cash", value: money(summary.cash)}, {label: "Today", value: money(summary.day_pl)}, {label: "Buying power", value: money(summary.buying_power)}]} />
        {portfolio.unavailable?.length ? <p className={styles.coverage} role="status">Refresh incomplete: {portfolio.unavailable.join(", ")}. Previous observations retained.</p> : null}
        {path.length ? <div className={styles.chart}><DataPlot displayModeBar={false} id="paper-portfolio-equity" title="Account equity · 1Y" data={[
          {type: "scatter", mode: "lines", name: "Account equity", x: path.map(p => p.date), y: path.map(p => p.equity), connectgaps: false, line: {color: teal, width: 2.4}},
        ]} layout={{xaxis: {type: "date"}, yaxis: {title: {text: "Equity (USD)"}, tickprefix: "$", tickformat: high - low > 10 || equityRange ? ",.0f" : ",.2f", range: equityRange, nticks: 4, automargin: true}, margin: {l: 85, r: 18, t: 42, b: 42}, showlegend: false, hovermode: "x unified", height: 330}} rowCount={path.length} /></div> : null}
        <MetricStrip items={[{label: "1Y return", value: pct(portfolio.return_pct)}, {label: "Equity drawdown", value: pct(portfolio.max_drawdown_pct)}, {label: "Long exposure", value: money(summary.long_market_value)}, {label: "Short exposure", value: money(summary.short_market_value)}]} />
      </>}
    </section>
    {available ? <section className="workbook-panel"><header className="panel-header"><h2>Positions</h2><span>{holdings.length} symbols</span></header>
      {holdings.length ? <DataTable<Record<string, unknown>> columns={[{key: "symbol", label: "Symbol"}, {key: "side", label: "Side"}, {key: "qty", label: "Shares"}, {key: "market_value_display", label: "Value"}, {key: "current_price_display", label: "Price"}, {key: "pl_display", label: "Unrealized P/L"}]} rows={holdings.map(row => ({...row, market_value_display: money(row.market_value), current_price_display: money(row.current_price), pl_display: money(row.unrealized_pl)}))} rowKey={row => String(row.symbol)} /> : <><p>No open positions.</p><button className="primary-button" type="button" onClick={onReviewCalls}>Review calls</button></>}
    </section> : null}
    {available ? <section className="workbook-panel"><h2>Recent paper orders</h2>{portfolio.orders?.length ? <DataTable columns={[{key: "symbol", label: "Symbol"}, {key: "side", label: "Side"}, {key: "qty", label: "Shares"}, {key: "filled_qty", label: "Filled"}, {key: "filled_avg_price", label: "Fill price"}, {key: "status", label: "Status"}, {key: "submitted_at", label: "Submitted"}]} rows={portfolio.orders} rowKey={row => String(row.id)} /> : <p>No paper orders yet.</p>}</section> : null}
    {calls.length ? <details className="workbook-panel"><summary>Research observations · {calls.length}</summary>
      <div className={styles.calls}>{calls.map(call => <button key={call.tracking_id} type="button" onClick={() => setSelected(call.tracking_id)}>
        <span><strong>{call.ticker}</strong> · {call.stance} · {call.horizon_label}<small>Approved {day(call.approved_at_utc)}</small></span><span>{pct(call.return_pct)} ›</span>
      </button>)}</div>
    </details> : null}
  </div>;
}

type RiskPolicy = {position_volatility_pct: number; portfolio_volatility_pct: number; max_position_pct: number; max_gross_pct: number};
type OrderReview = {ticket_id: string; arguments_hash: string; expires_at: string; order: {symbol: string; side: string; qty: string; type: string; time_in_force: string}; risk: {suggested_notional: number; asset_volatility_pct: number; portfolio_volatility_before_pct: number; portfolio_volatility_after_pct: number; observations: number; history_start: string; history_end: string; binding_limit: string; execution_price_tolerance_pct: number}};
export function PaperOrderReview({candidate, initialPolicy, onClose, onSubmitted}: {candidate: {candidate_id: string; ticker: string}; initialPolicy?: RiskPolicy; onClose: () => void; onSubmitted: (order: Record<string, string>) => Promise<void>}) {
  const [policy, setPolicy] = useState<RiskPolicy | null>(initialPolicy || null);
  const [review, setReview] = useState<OrderReview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    return () => element?.close();
  }, []);
  async function calculate() {
    if (!policy) {setError("Risk limits are unavailable. Refresh the paper account."); return;}
    setBusy(true); setError(""); setReview(null);
    try {
      const response = await secureMutation("/api/trading-agent/paper/review", {method: "POST", body: JSON.stringify({candidate_id: candidate.candidate_id, policy})});
      if (!response.ok) throw new Error(await responseError(response, "The paper order could not be sized."));
      setReview(await response.json() as OrderReview);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sizing is unavailable."); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!review) return;
    setBusy(true); setError("");
    try {
      const response = await secureMutation("/api/trading-agent/paper/confirm", {method: "POST", body: JSON.stringify({ticket_id: review.ticket_id, arguments_hash: review.arguments_hash})});
      if (!response.ok) throw new Error(await responseError(response, "Paper order submission is unavailable."));
      const receipt = await response.json() as {order: Record<string, string>};
      await onSubmitted(receipt.order);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Submission is unavailable."); }
    finally { setBusy(false); }
  }
  const labels: Record<keyof RiskPolicy, string> = {position_volatility_pct: "Position volatility budget (%)", portfolio_volatility_pct: "Portfolio volatility limit (%)", max_position_pct: "Maximum position (% equity)", max_gross_pct: "Maximum gross exposure (%)"};
  return <dialog ref={dialog} className={styles.overlay} aria-label={`Review ${candidate.ticker} paper trade`} onCancel={event => {event.preventDefault(); if (!busy) onClose();}}><section className={`workbook-panel ${styles.review}`}>
    <header className="panel-header"><h2>{candidate.ticker} · paper trade</h2><button className="quiet-button" type="button" disabled={busy} onClick={onClose}>Close</button></header>
    <p className={styles.method}>Volatility budgeting · Ledoit–Wolf covariance · annualized risk</p>
    {policy ? <div className={styles.riskInputs}>{(Object.keys(labels) as Array<keyof RiskPolicy>).map(key => <label key={key}>{labels[key]}<input type="number" min="0.1" max="100" step="0.1" value={policy[key]} disabled={busy} onChange={event => {setPolicy({...policy, [key]: Number(event.target.value)}); setReview(null);}} /></label>)}</div> : <p role="alert">Risk limits are unavailable. Refresh the paper account.</p>}
    {error ? <p className="subtle-error" role="alert">{error}</p> : null}
    {review ? <>
      <h3>{review.order.side === "buy" ? "Buy" : "Short"} {review.order.qty} {review.order.symbol}</h3>
      <p>{review.order.type} · {review.order.time_in_force} · {review.risk.execution_price_tolerance_pct}% price reserve</p>
      <MetricStrip items={[{label: "Estimated amount", value: money(review.risk.suggested_notional)}, {label: "Estimated portfolio volatility", value: `${pct(review.risk.portfolio_volatility_before_pct)} → ${pct(review.risk.portfolio_volatility_after_pct)}`}]} />
      <p>{review.risk.observations} daily returns · {day(review.risk.history_start)}–{day(review.risk.history_end)}</p>
      <p>Limited by {review.risk.binding_limit.replaceAll("_", " ")} · review expires {new Date(review.expires_at).toLocaleTimeString()}</p>
      <button className="primary-button" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? "Submitting…" : error ? "Retry confirmation" : "Place paper order"}</button>
      <button className="quiet-button" type="button" disabled={busy} onClick={() => void calculate()}>Recalculate size</button>
    </> : <button className="primary-button" type="button" disabled={busy || !policy} onClick={() => void calculate()}>{busy ? "Calculating…" : "Calculate size"}</button>}
  </section></dialog>;
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
