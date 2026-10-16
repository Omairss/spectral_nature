"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";

type Candidate = Record<string, unknown> & {
  candidate_id: string; ticker: string; stance: string; direction: string; research_status: string;
  setup: string; hypothesis: string; invalidation: string; suggested_horizon: string; confidence: string;
  evidence: string[]; tail_risks: string[]; evidence_limits: string[]; market: Record<string, unknown>;
  decision: Record<string, unknown>; forecast?: Forecast;
};
type Horizon = { key: string; label: string; selected_horizon_col: string; status: string; regime_read: string; portfolio_posture: string; data_gaps: string[]; candidates: Candidate[] };
type Forecast = { reference_price?: number; reference_snapshot_as_of: string; pricing_assessment: string; assumptions: string[]; opposing_case: string };
type TrackedCall = {tracking_id: string; ticker: string; stance: string; horizon_label: string; approved_at_utc: string; as_of?: string; status: string; return_pct?: number; benchmark_return_pct?: number; excess_return_pct?: number; max_drawdown_pct?: number; entry_date?: string; last_price_date?: string; target_date?: string; hypothesis?: string; path?: Array<{date: string; return_pct: number; benchmark_return_pct: number | null}>};
type TradingView = { status: string; as_of: string; run_id: string; horizons: Horizon[]; decisions: Array<Record<string, unknown>>; tracked_calls?: TrackedCall[]; tracking_status?: string };
function number(value: unknown) { if (value === null || value === undefined || value === "") return null; const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(1)}%`; }

export default function TradingAgentPage() {
  const { session, sessionError } = useAuthenticatedSession("/trading-agent");
  const [view, setView] = useState<TradingView | null>(null);
  const [activeHorizon, setActiveHorizon] = useState("");
  const [section, setSection] = useState("calls");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/trading-agent", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error(await responseError(response, response.status === 403 ? "Admin access is required." : "Trading Agent data could not be loaded."));
      const next = (await response.json()) as TradingView;
      setView(next);
      setActiveHorizon((current) => next.horizons.some((item) => item.key === current) ? current : next.horizons[0]?.key || "");
    } catch (reason) { setView(null); setError(reason instanceof Error ? reason.message : "Trading Agent data could not be loaded."); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);
  const horizon = view?.horizons.find((item) => item.key === activeHorizon);
  const groups = useMemo(() => ({
    long: horizon?.candidates.filter((item) => item.stance === "long") || [],
    short: horizon?.candidates.filter((item) => item.stance === "short") || [],
    watch: horizon?.candidates.filter((item) => !["long", "short"].includes(item.stance)) || [],
  }), [horizon]);

  async function decide(candidate: Candidate, action: "approve" | "reject") {
    setLoading(true); setError("");
    try {
      const response = await secureMutation("/api/trading-agent/decisions", { method: "POST", body: JSON.stringify({ candidate_id: candidate.candidate_id, action }) });
      if (!response.ok) throw new Error(await responseError(response, "Decision could not be recorded."));
      await load();
      if (action === "approve") setSection("performance");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Decision could not be recorded."); setLoading(false); }
  }


  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page">
      <header className="artifact-header"><div><p className="breadcrumb">Agent <span>/</span> Market decisions</p><h1>Trading Agent</h1></div>{view?.as_of ? <time dateTime={view.as_of}>{new Date(view.as_of).toLocaleString()}</time> : null}</header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !view ? <p className="loading-label">Loading Trading Agent…</p> : null}
      <WorkbookTabs active={section} items={[{key: "calls", label: "Calls"}, {key: "performance", label: "Paper performance"}]} label="Trading view" onChange={setSection} />
      {section === "performance" ? <ApprovedCallPerformance calls={view?.tracked_calls || []} unavailable={view?.tracking_status === "unavailable"} /> : view?.status === "available" && horizon ? <>
        <WorkbookTabs active={activeHorizon} items={view.horizons.map((item) => ({ key: item.key, label: item.label }))} label="Trading horizons" onChange={setActiveHorizon} />
        <section className="agent-backdrop"><p className="section-label">Market backdrop</p><h2>{horizon.regime_read}</h2>{horizon.portfolio_posture ? <p>{horizon.portfolio_posture}</p> : null}</section>
        <MetricStrip items={[{ label: "Long", value: groups.long.length }, { label: "Short", value: groups.short.length }, { label: "Watch", value: groups.watch.length }, { label: "Decisions", value: view.decisions.length }]} />
        {groups.long.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Long</p><h2>Long calls</h2></div><span>{groups.long.length}</span></header><div className="candidate-grid">{groups.long.map((candidate) => <CandidateCard candidate={candidate} horizon={horizon} loading={loading} onDecide={decide} key={candidate.candidate_id} />)}</div></section> : null}
        {groups.short.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Short</p><h2>Short calls</h2></div><span>{groups.short.length}</span></header><div className="candidate-grid">{groups.short.map((candidate) => <CandidateCard candidate={candidate} horizon={horizon} loading={loading} onDecide={decide} key={candidate.candidate_id} />)}</div></section> : null}
        {groups.watch.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Watch</p><h2>Research queue</h2></div><span>{groups.watch.length}</span></header><div className="candidate-grid">{groups.watch.map((candidate) => <CandidateCard candidate={candidate} horizon={horizon} loading={loading} onDecide={decide} key={candidate.candidate_id} />)}</div></section> : null}
        {view.decisions.length ? <details className="workbook-panel decisions-panel"><summary>Decisions</summary><DataTable columns={[{ key: "created_at_utc", label: "Time" }, { key: "ticker", label: "Ticker" }, { key: "horizon_key", label: "Horizon" }, { key: "action", label: "Decision" }, { key: "requested_email", label: "Reviewed by" }]} rowKey={(row, index) => String(row.action_id || index)} rows={view.decisions} /></details> : null}
      </> : view && !loading ? <section className="empty-state"><strong>Trading Agent unavailable</strong></section> : null}
    </div>
  );
}

function CandidateCard({ candidate, horizon, loading, onDecide }: { candidate: Candidate; horizon?: Horizon; loading: boolean; onDecide: (candidate: Candidate, action: "approve" | "reject") => Promise<void> }) {
    const router = useRouter();
    const [expanded, setExpanded] = useState(false);
    const market = candidate.market || {};
    const decision = String(candidate.decision?.action || "");
    return (
      <article className={`candidate-card stance-${candidate.stance}`}><details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}><summary>
        <header><div><span className="candidate-symbol">{candidate.ticker}</span><span className="status-badge">{candidate.confidence}</span></div><div><strong>{candidate.direction}</strong><small>{candidate.suggested_horizon}</small></div></header>
        <p>{candidate.setup}</p></summary>
        {expanded ? <>
        <div className="candidate-market"><span>{String(market.company_name || "")}</span><span>1D {pct(market.daily_change_pct)}</span><span>{String(market.selected_horizon_label || horizon?.label || "Return")} {pct(market[horizon?.selected_horizon_col || ""])}</span><span>{String(market.direction || "")}</span></div>
        {Array.isArray(market.sparkline_3m) && market.sparkline_3m.length ? <div className="candidate-spark"><DataPlot data={[{ type: "scatter", mode: "lines", x: market.sparkline_3m.map((_, index) => index), y: market.sparkline_3m, line: { color: "#52d6c7", width: 1.8 }, hoverinfo: "skip" }]} id={`candidate-${candidate.candidate_id}`} layout={{ margin: { l: 8, r: 8, t: 8, b: 8 }, xaxis: { visible: false }, yaxis: { visible: false } }} rowCount={market.sparkline_3m.length} title="" /></div> : null}
        <p>{candidate.hypothesis}</p>
        {candidate.forecast ? <div className="candidate-list">
          {candidate.forecast.reference_price ? <small>Reference ${candidate.forecast.reference_price.toFixed(2)} · snapshot {candidate.forecast.reference_snapshot_as_of.slice(0, 10)}</small> : null}
          <strong>At this price</strong><p>{candidate.forecast.pricing_assessment}</p>
          {candidate.forecast.assumptions.length ? <><strong>Assumptions</strong><ul>{candidate.forecast.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
          <strong>Opposing case</strong><p>{candidate.forecast.opposing_case}</p>
        </div> : null}
        {candidate.evidence.length ? <div className="candidate-list"><strong>Evidence</strong><ul>{candidate.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        <div className="candidate-columns">
          <div><strong>Invalidation</strong><p>{candidate.invalidation}</p></div>
          {candidate.tail_risks.length ? <div><strong>Risks</strong><ul>{candidate.tail_risks.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
          {candidate.evidence_limits.length ? <div><strong>Research next</strong><ul>{candidate.evidence_limits.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        </div>
        <footer>
          <button className="quiet-button" onClick={() => router.push(`/stock-investigator?ticker=${encodeURIComponent(candidate.ticker)}`)} type="button">Open {candidate.ticker}</button>
          {decision ? <span className="decision-state">{["place_requested", "approved"].includes(decision) ? "Approved" : decision === "rejected" ? "Rejected" : decision}</span> : <div className="decision-actions"><button className="quiet-button" disabled={loading} onClick={() => void onDecide(candidate, "reject")} type="button">Reject</button><button className="primary-button" disabled={loading || !["long", "short"].includes(candidate.stance)} onClick={() => void onDecide(candidate, "approve")} type="button">Approve and track</button></div>}
        </footer>
      </> : null}</details></article>
    );
  }


function ApprovedCallPerformance({calls, unavailable}: {calls: TrackedCall[]; unavailable: boolean}) {
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<TrackedCall | null>(null);
  const [error, setError] = useState("");
  const chosen = calls.find((item) => item.tracking_id === selected) || calls[0];
  const identity = chosen?.tracking_id || "";
  useEffect(() => {
    const controller = new AbortController();
    if (!identity) return () => controller.abort();
    void fetch(`/api/trading-agent/calls/${encodeURIComponent(identity)}`, {cache: "no-store", signal: controller.signal}).then(async (response) => {
      if (!response.ok) throw new Error(await responseError(response, "Call tracking is unavailable."));
      const value = await response.json() as TrackedCall;
      if (!controller.signal.aborted) {setDetail(value); setError("");}
    }).catch((reason: unknown) => {if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Call tracking is unavailable.");});
    return () => controller.abort();
  }, [identity, chosen?.as_of]);
  if (unavailable) return <p className="subtle-error" role="alert">Call tracking is unavailable.</p>;
  if (!chosen) return <section className="workbook-panel"><p>Approve a call to track its performance.</p></section>;
  const current = detail?.tracking_id === identity ? detail : chosen;
  const path = current.path || [];
  const benchmark = current.stance === "short" ? "SPY short" : "SPY long";
  return <section className="workbook-panel">
    <MetricStrip items={[{label: "Tracked calls", value: calls.length}, {label: "Open", value: calls.filter((item) => item.status === "open").length}, {label: "Completed", value: calls.filter((item) => item.status === "matured").length}]} />
    <label>Approved call <select value={identity} onChange={(event) => setSelected(event.target.value)}>{calls.map((item) => <option key={item.tracking_id} value={item.tracking_id}>{item.ticker} · {item.stance} · {item.horizon_label} · {new Date(item.approved_at_utc).toLocaleDateString()}</option>)}</select></label>
    {error ? <p className="subtle-error" role="alert">{error}</p> : null}
    <MetricStrip items={[{label: "Return", value: pct(current.return_pct)}, {label: `Vs ${benchmark}`, value: pct(current.excess_return_pct)}, {label: "Drawdown", value: pct(current.max_drawdown_pct)}]} />
    {current.status === "pending_entry" ? <p>Entry at the next trading day’s close.</p> : null}
    {current.status === "price_unavailable" ? <p>Price data is unavailable.</p> : null}
    {path.length ? <DataPlot id={`tracked-${identity}`} title={`${current.ticker} · paper return`} data={[
      {type: "scatter", mode: path.length === 1 ? "markers" : "lines", name: "Call", x: path.map((point) => point.date), y: path.map((point) => point.return_pct), line: {color: "#52d6c7"}},
      {type: "scatter", mode: "lines", name: benchmark, x: path.map((point) => point.date), y: path.map((point) => point.benchmark_return_pct), connectgaps: false, line: {color: "#b89cff", dash: "dot"}},
    ]} layout={{xaxis: {type: "date"}, yaxis: {title: {text: "Return (%)"}, zeroline: true}, showlegend: true}} rowCount={path.length} /> : null}
    {current.entry_date ? <p>Entry {current.entry_date} · Target {current.target_date} · Prices through {current.last_price_date}</p> : null}
    {current.hypothesis ? <p>{current.hypothesis}</p> : null}
    <DataTable columns={[{key: "ticker", label: "Symbol"}, {key: "stance", label: "Call"}, {key: "horizon_label", label: "Horizon"}, {key: "return_pct", label: "Return (%)"}, {key: "status", label: "Status"}]} rows={calls as unknown as Record<string, unknown>[]} rowKey={(row) => String(row.tracking_id)} />
  </section>;
}
