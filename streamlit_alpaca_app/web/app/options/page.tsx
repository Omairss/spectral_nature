"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { fetchDataset, responseError } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";
import type { StockInvestigatorView } from "@/lib/types";

type OptionRow = Record<string, unknown> & { contractSymbol?: string; type?: string; expiration?: string; strike?: number; dte?: number };
type CandidatePayload = { candidates?: OptionRow[]; summary?: Record<string, unknown> };
function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function money(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `$${parsed.toLocaleString(undefined, { maximumFractionDigits: 2 })}`; }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(1)}%`; }
function decimal(value: unknown, digits = 3) { const parsed = number(value); return parsed === null ? "—" : parsed.toFixed(digits); }
function tickerValue(value: string) { const normalized = value.trim().toUpperCase(); return /^[A-Z0-9.-]{1,12}$/.test(normalized) ? normalized : "AAPL"; }

export default function OptionsPage() {
  const router = useRouter();
  const { session, sessionError } = useAuthenticatedSession("/options");
  const [ticker, setTicker] = useState("AAPL");
  const [activeTicker, setActiveTicker] = useState("AAPL");
  const [expirations, setExpirations] = useState<string[]>([]);
  const [expiration, setExpiration] = useState("");
  const [calls, setCalls] = useState<OptionRow[]>([]);
  const [puts, setPuts] = useState<OptionRow[]>([]);
  const [contractType, setContractType] = useState("call");
  const [underlying, setUnderlying] = useState<number | null>(null);
  const [expectedPrice, setExpectedPrice] = useState(0);
  const [horizon, setHorizon] = useState(30);
  const [surface, setSurface] = useState<OptionRow[]>([]);
  const [candidates, setCandidates] = useState<OptionRow[]>([]);
  const [summary, setSummary] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadChain = useCallback(async (symbol: string, selectedExpiration = "") => {
    const result = await fetchDataset<unknown[]>("option_chain", { ticker: symbol, expiration: selectedExpiration || null });
    const raw = Array.isArray(result.payload) ? result.payload : [];
    const expiryValues = Array.isArray(raw[0]) ? raw[0].map(String) : [];
    const callRows = Array.isArray(raw[1]) ? raw[1].filter((row): row is OptionRow => Boolean(row) && typeof row === "object") : [];
    const putRows = Array.isArray(raw[2]) ? raw[2].filter((row): row is OptionRow => Boolean(row) && typeof row === "object") : [];
    setExpirations(expiryValues);
    setCalls(callRows);
    setPuts(putRows);
    return expiryValues;
  }, []);

  const loadSymbol = useCallback(async (symbol: string) => {
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      const contextResponse = await fetch(`/api/investigator/${encodeURIComponent(symbol)}?days=180`, { cache: "no-store", credentials: "same-origin" });
      if (!contextResponse.ok) throw new Error(await responseError(contextResponse, "Underlying price could not be loaded."));
      const context = (await contextResponse.json()) as StockInvestigatorView;
      const current = number(context.technical.close ?? context.forecast.current_price);
      if (current === null || current <= 0) throw new Error("Underlying price is unavailable.");
      setUnderlying(current);
      setExpectedPrice(Number((current * 1.05).toFixed(2)));
      const expiryValues = await loadChain(symbol);
      const initial = expiryValues[0] || "";
      setExpiration(initial);
      if (initial) await loadChain(symbol, initial);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Options data could not be loaded."); }
    finally { setLoading(false); }
  }, [loadChain]);

  const runScenario = useCallback(async () => {
    if (!underlying || !expectedPrice) return;
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      const [surfaceResult, candidatesResult] = await Promise.all([
        fetchDataset<OptionRow[]>("option_surface", { ticker: activeTicker, expected_price: expectedPrice, horizon_days: horizon, underlying_price: underlying }),
        fetchDataset<CandidatePayload>("option_candidates", { ticker: activeTicker, expected_price: expectedPrice, horizon_days: horizon, underlying_price: underlying }),
      ]);
      setSurface(surfaceResult.payload || []);
      setCandidates(candidatesResult.payload?.candidates || []);
      setSummary(candidatesResult.payload?.summary || {});
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Option scenario could not be evaluated."); }
    finally { setLoading(false); }
  }, [activeTicker, expectedPrice, horizon, underlying]);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void loadSymbol(activeTicker); }, 0);
    return () => window.clearTimeout(timer);
  }, [activeTicker, loadSymbol, session]);
  useEffect(() => {
    if (!session?.authenticated || !underlying || !expectedPrice) return;
    const timer = window.setTimeout(() => { void runScenario(); }, 0);
    return () => window.clearTimeout(timer);
  }, [expectedPrice, horizon, runScenario, session, underlying]);

  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setActiveTicker(tickerValue(ticker)); setTicker(tickerValue(ticker)); }
  async function changeExpiration(value: string) { setExpiration(value); setLoading(true); setError(""); try { await loadChain(activeTicker, value); } catch (reason) { setError(reason instanceof Error ? reason.message : "Option chain could not be loaded."); } finally { setLoading(false); } }
  const chainRows = contractType === "call" ? calls : puts;
  const best = candidates[0];
  const strikes = Array.from(new Set(surface.map((row) => number(row.strike)).filter((value): value is number => value !== null))).sort((a, b) => a - b);
  const dtes = Array.from(new Set(surface.map((row) => number(row.dte)).filter((value): value is number => value !== null))).sort((a, b) => a - b);
  const ivGrid = dtes.map((dte) => strikes.map((strike) => number(surface.find((row) => number(row.dte) === dte && number(row.strike) === strike)?.impliedVolatility)));

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page wide-workbook">
      <header className="artifact-header workbook-heading"><div><p className="breadcrumb">Research <span>/</span> Derivatives</p><h1>Option Strategizer</h1></div>
        <form className="inline-controls" onSubmit={submit}><label>Symbol<input maxLength={12} onChange={(event) => setTicker(event.target.value.toUpperCase())} value={ticker} /></label><button className="primary-button" disabled={loading} type="submit">Load</button></form>
      </header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {underlying ? <>
        <MetricStrip items={[
          { label: "Underlying", value: money(underlying) }, { label: "Expected price", value: money(expectedPrice) },
          { label: "Expected move", value: pct(((expectedPrice / underlying) - 1) * 100), tone: expectedPrice > underlying ? "positive" : "" },
          { label: "Horizon", value: `${horizon} days` }, { label: "Expirations", value: expirations.length }, { label: "Candidates", value: candidates.length },
        ]} />
        <section className="workbook-panel">
          <div className="control-deck options-controls">
            <label>Expiration<select onChange={(event) => void changeExpiration(event.target.value)} value={expiration}>{expirations.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label>Expected price<input min="0.01" onChange={(event) => setExpectedPrice(Number(event.target.value))} step="0.01" type="number" value={expectedPrice} /></label>
            <label>Horizon<select onChange={(event) => setHorizon(Number(event.target.value))} value={horizon}>{[7, 14, 30, 60, 90].map((value) => <option key={value} value={value}>{value} days</option>)}</select></label>
            <button className="quiet-button" disabled={loading} onClick={() => void runScenario()} type="button">Evaluate</button>
          </div>
          <WorkbookTabs active={contractType} items={[{ key: "call", label: `Calls · ${calls.length}` }, { key: "put", label: `Puts · ${puts.length}` }]} label="Contract type" onChange={setContractType} />
          <DataTable<OptionRow> columns={[
            { key: "contractSymbol", label: "Contract" }, { key: "strike", label: "Strike", align: "right", render: money },
            { key: "bid", label: "Bid", align: "right", render: money }, { key: "ask", label: "Ask", align: "right", render: money },
            { key: "lastPrice", label: "Last", align: "right", render: money }, { key: "impliedVolatility", label: "IV", align: "right", render: (value) => { const parsed = number(value); return parsed === null ? "—" : `${(parsed * 100).toFixed(1)}%`; } },
            { key: "openInterest", label: "Open interest", align: "right" }, { key: "volume", label: "Volume", align: "right" },
            { key: "delta", label: "Delta", align: "right", render: (value) => decimal(value) }, { key: "theta", label: "Theta", align: "right", render: (value) => decimal(value) },
          ]} rowKey={(row, index) => String(row.contractSymbol || index)} rows={chainRows.slice(0, 160)} />
        </section>
        {surface.length ? <section className="workbook-panel split-visuals">
          <div className="workbook-chart"><DataPlot data={strikes.length > 1 && dtes.length > 1 ? [{ type: "surface", x: strikes, y: dtes, z: ivGrid, colorscale: "Viridis", colorbar: { title: "IV" } }] : [{ type: "scatter3d", mode: "markers", x: surface.map((row) => number(row.strike)), y: surface.map((row) => number(row.dte)), z: surface.map((row) => number(row.impliedVolatility)), text: surface.map((row) => row.contractSymbol), marker: { size: 4, color: surface.map((row) => number(row.impliedVolatility)), colorscale: "Viridis", showscale: true } }]} id={`option-iv-${activeTicker}-${horizon}`} layout={{ scene: { xaxis: { title: "Strike" }, yaxis: { title: "DTE" }, zaxis: { title: "IV" } } }} rowCount={surface.length} title={`${activeTicker} implied-volatility surface`} /></div>
          <div className="workbook-chart"><DataPlot data={[{ type: "scatter3d", mode: "markers", x: candidates.map((row) => number(row.strike)), y: candidates.map((row) => number(row.dte)), z: candidates.map((row) => number(row.projected_return_pct)), text: candidates.map((row) => row.contractSymbol), marker: { size: 5, color: candidates.map((row) => number(row.selection_score)), colorscale: "Plasma", showscale: true } }]} id={`option-opportunity-${activeTicker}-${horizon}`} layout={{ scene: { xaxis: { title: "Strike" }, yaxis: { title: "DTE" }, zaxis: { title: "Projected return %" } } }} rowCount={candidates.length} title="Opportunity surface" /></div>
        </section> : null}
        {best ? <section className="workbook-panel">
          <header className="panel-header"><div><p className="section-label">Best fit</p><h2>{String(best.name || best.contractSymbol || "Contract")}</h2></div><button className="quiet-button" onClick={() => router.push(`/stock-investigator?ticker=${encodeURIComponent(activeTicker)}`)} type="button">Open {activeTicker}</button></header>
          <MetricStrip items={[
            { label: "Premium", value: money(best.premium) }, { label: "Contract cost", value: money(best.contracts_cost) },
            { label: "Projected P/L", value: money(best.projected_pnl), tone: (number(best.projected_pnl) || 0) > 0 ? "positive" : "" },
            { label: "Projected return", value: pct(best.projected_return_pct) }, { label: "Breakeven", value: money(best.breakeven) }, { label: "Selection score", value: decimal(best.selection_score, 1) },
          ]} />
          <div className="workbook-chart"><DataPlot data={[{ type: "scatter", mode: "markers+text", x: candidates.map((row) => number(row.delta)), y: candidates.map((row) => number(row.theta)), text: candidates.map((row) => row.contractSymbol), marker: { size: candidates.map((row) => Math.max(7, Math.min(34, Math.abs(number(row.gamma) || 0) * 1100))), color: candidates.map((row) => number(row.vega)), colorscale: "Turbo", showscale: true } }]} id={`option-greeks-${activeTicker}-${horizon}`} layout={{ xaxis: { title: "Delta" }, yaxis: { title: "Theta" } }} rowCount={candidates.length} title="Greek tradeoffs" /></div>
          <DataTable<OptionRow> columns={[
            { key: "contractSymbol", label: "Contract" }, { key: "type", label: "Type" }, { key: "expiration", label: "Expiration" },
            { key: "strike", label: "Strike", align: "right", render: money }, { key: "premium", label: "Premium", align: "right", render: money },
            { key: "projected_return_pct", label: "Projected return", align: "right", render: pct }, { key: "delta", label: "Delta", align: "right", render: (value) => decimal(value) },
            { key: "theta", label: "Theta", align: "right", render: (value) => decimal(value) }, { key: "selection_score", label: "Score", align: "right", render: (value) => decimal(value, 1) },
          ]} rowKey={(row, index) => String(row.contractSymbol || index)} rows={candidates.slice(0, 80)} />
        </section> : null}
        {Object.keys(summary).length ? <details className="provenance-panel"><summary>Scenario summary</summary><pre>{JSON.stringify(summary, null, 2)}</pre></details> : null}
      </> : loading ? <p className="loading-label">Loading option chain…</p> : null}
    </div>
  );
}
