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
  decision: Record<string, unknown>;
};
type Horizon = { key: string; label: string; selected_horizon_col: string; status: string; regime_read: string; portfolio_posture: string; data_gaps: string[]; candidates: Candidate[] };
type TradingView = { status: string; as_of: string; run_id: string; horizons: Horizon[]; decisions: Array<Record<string, unknown>> };
function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(1)}%`; }

export default function TradingAgentPage() {
  const router = useRouter();
  const { session, sessionError } = useAuthenticatedSession("/trading-agent");
  const [view, setView] = useState<TradingView | null>(null);
  const [activeHorizon, setActiveHorizon] = useState("");
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
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Decision could not be recorded."); setLoading(false); }
  }

  function CandidateCard({ candidate }: { candidate: Candidate }) {
    const market = candidate.market || {};
    const decision = String(candidate.decision?.action || "");
    return (
      <article className={`candidate-card stance-${candidate.stance}`}>
        <header><div><span className="candidate-symbol">{candidate.ticker}</span><span className="status-badge">{candidate.confidence}</span></div><div><strong>{candidate.direction}</strong><small>{candidate.suggested_horizon}</small></div></header>
        <div className="candidate-market"><span>{String(market.company_name || "")}</span><span>1D {pct(market.daily_change_pct)}</span><span>{String(market.selected_horizon_label || horizon?.label || "Return")} {pct(market[horizon?.selected_horizon_col || ""])}</span><span>{String(market.direction || "")}</span></div>
        {Array.isArray(market.sparkline_3m) && market.sparkline_3m.length ? <div className="candidate-spark"><DataPlot data={[{ type: "scatter", mode: "lines", x: market.sparkline_3m.map((_, index) => index), y: market.sparkline_3m, line: { color: "#52d6c7", width: 1.8 }, hoverinfo: "skip" }]} id={`candidate-${candidate.candidate_id}`} layout={{ margin: { l: 8, r: 8, t: 8, b: 8 }, xaxis: { visible: false }, yaxis: { visible: false } }} rowCount={market.sparkline_3m.length} title="" /></div> : null}
        <h3>{candidate.setup}</h3>
        <p>{candidate.hypothesis}</p>
        {candidate.evidence.length ? <div className="candidate-list"><strong>Evidence</strong><ul>{candidate.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        <div className="candidate-columns">
          <div><strong>Invalidation</strong><p>{candidate.invalidation}</p></div>
          {candidate.tail_risks.length ? <div><strong>Risks</strong><ul>{candidate.tail_risks.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
          {candidate.evidence_limits.length ? <div><strong>Research next</strong><ul>{candidate.evidence_limits.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        </div>
        <footer>
          <button className="quiet-button" onClick={() => router.push(`/stock-investigator?ticker=${encodeURIComponent(candidate.ticker)}`)} type="button">Open {candidate.ticker}</button>
          {decision ? <span className="decision-state">{decision === "place_requested" ? "Approved" : decision === "rejected" ? "Rejected" : decision}</span> : <div className="decision-actions"><button className="quiet-button" disabled={loading} onClick={() => void decide(candidate, "reject")} type="button">Reject</button><button className="primary-button" disabled={loading} onClick={() => void decide(candidate, "approve")} type="button">Approve</button></div>}
        </footer>
      </article>
    );
  }

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page">
      <header className="artifact-header"><div><p className="breadcrumb">Agent <span>/</span> Market decisions</p><h1>Trading Agent</h1></div>{view?.as_of ? <time dateTime={view.as_of}>{new Date(view.as_of).toLocaleString()}</time> : null}</header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !view ? <p className="loading-label">Loading Trading Agent…</p> : null}
      {view?.status === "available" && horizon ? <>
        <WorkbookTabs active={activeHorizon} items={view.horizons.map((item) => ({ key: item.key, label: item.label }))} label="Trading horizons" onChange={setActiveHorizon} />
        <section className="agent-backdrop"><p className="section-label">Market backdrop</p><h2>{horizon.regime_read}</h2>{horizon.portfolio_posture ? <p>{horizon.portfolio_posture}</p> : null}</section>
        <MetricStrip items={[{ label: "Long", value: groups.long.length }, { label: "Short", value: groups.short.length }, { label: "Watch", value: groups.watch.length }, { label: "Decisions", value: view.decisions.length }]} />
        {groups.long.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Long</p><h2>Supported upside</h2></div><span>{groups.long.length}</span></header><div className="candidate-grid">{groups.long.map((candidate) => <CandidateCard candidate={candidate} key={candidate.candidate_id} />)}</div></section> : null}
        {groups.short.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Short</p><h2>Supported downside</h2></div><span>{groups.short.length}</span></header><div className="candidate-grid">{groups.short.map((candidate) => <CandidateCard candidate={candidate} key={candidate.candidate_id} />)}</div></section> : null}
        {groups.watch.length ? <section className="candidate-section"><header className="panel-header"><div><p className="section-label">Watch</p><h2>Research queue</h2></div><span>{groups.watch.length}</span></header><div className="candidate-grid">{groups.watch.map((candidate) => <CandidateCard candidate={candidate} key={candidate.candidate_id} />)}</div></section> : null}
        {view.decisions.length ? <details className="workbook-panel decisions-panel"><summary>Decisions</summary><DataTable columns={[{ key: "created_at_utc", label: "Time" }, { key: "ticker", label: "Ticker" }, { key: "horizon_key", label: "Horizon" }, { key: "action", label: "Decision" }, { key: "requested_email", label: "Reviewed by" }]} rowKey={(row, index) => String(row.action_id || index)} rows={view.decisions} /></details> : null}
      </> : view && !loading ? <section className="empty-state"><strong>Trading Agent unavailable</strong></section> : null}
    </div>
  );
}
