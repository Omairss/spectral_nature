"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { fetchDataset, responseError } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";

type Lens = { name: string; description: string; symbol_count: number; symbols: string[] };
type Horizon = { key: string; label: string; column: string };
type LensView = { business: Lens[]; commodities: Lens[]; horizons: Horizon[] };
type MarketRow = Record<string, unknown> & {
  symbol: string; company_name: string; opportunity: string; direction: string; opportunity_score: number;
  close: number; volume: number; daily_change_pct: number; return_1m_pct: number; return_3m_pct: number;
  return_1y_pct: number; return_5y_pct: number; momentum_score: number; momentum_roc_score: number; trend_fit_gap: number;
};

function numeric(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function pct(value: unknown) { const parsed = numeric(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${parsed.toFixed(1)}%`; }
function money(value: unknown) { const parsed = numeric(value); return parsed === null ? "—" : `$${parsed.toLocaleString(undefined, { maximumFractionDigits: 2 })}`; }

export default function MarketExplorerPage() {
  const router = useRouter();
  const { session, sessionError } = useAuthenticatedSession("/market-explorer");
  const [lenses, setLenses] = useState<LensView | null>(null);
  const [mode, setMode] = useState("markets");
  const [business, setBusiness] = useState("All Market");
  const [commodity, setCommodity] = useState("Broad Commodity Market");
  const [horizon, setHorizon] = useState("1m");
  const [rows, setRows] = useState<MarketRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!session?.authenticated) return;
    fetch("/api/market-lenses", { cache: "no-store", credentials: "same-origin" })
      .then(async (response) => { if (!response.ok) throw new Error(await responseError(response, "Market lenses could not be loaded.")); return response.json() as Promise<LensView>; })
      .then(setLenses).catch((reason) => setError(reason instanceof Error ? reason.message : "Market lenses could not be loaded."));
  }, [session]);

  const selectedHorizon = lenses?.horizons.find((item) => item.key === horizon) || { key: "1m", label: "1 Month", column: "return_1m_pct" };
  const selectedLens = mode === "commodities"
    ? lenses?.commodities.find((item) => item.name === commodity)
    : lenses?.business.find((item) => item.name === business);

  const load = useCallback(async () => {
    if (!lenses) return;
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      if (mode === "markets") {
        const result = await fetchDataset<MarketRow[]>("market_opportunity_feed", {
          business_filter: business,
          selected_horizon_col: selectedHorizon.column,
          selected_horizon_label: selectedHorizon.label,
          symbols: selectedLens?.symbols || [],
          limit: 80,
        });
        setRows(result.payload || []);
      } else {
        const result = await fetchDataset<MarketRow[]>("momentum_profiles", { days: 3650, symbols: selectedLens?.symbols || [] });
        setRows(result.payload || []);
      }
    } catch (reason) { setRows([]); setError(reason instanceof Error ? reason.message : "Market data could not be loaded."); }
    finally { setLoading(false); }
  }, [business, lenses, mode, selectedHorizon.column, selectedHorizon.label, selectedLens]);

  useEffect(() => {
    if (!session?.authenticated || !lenses) return;
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, lenses, session]);
  const sortedRows = useMemo(() => [...rows].sort((left, right) => (numeric(right[selectedHorizon.column]) || -Infinity) - (numeric(left[selectedHorizon.column]) || -Infinity)), [rows, selectedHorizon.column]);
  const advancing = rows.filter((row) => (numeric(row[selectedHorizon.column]) || 0) > 0).length;
  const average = rows.length ? rows.reduce((sum, row) => sum + (numeric(row[selectedHorizon.column]) || 0), 0) / rows.length : null;
  const title = mode === "commodities" ? commodity : business;
  const horizonColumns = lenses?.horizons.map((item) => item.column) || [];

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page wide-workbook">
      <header className="artifact-header workbook-heading"><div><p className="breadcrumb">Research <span>/</span> Cross-asset</p><h1>Market Explorer</h1></div></header>
      <WorkbookTabs active={mode} items={[{ key: "markets", label: "Markets" }, { key: "broad", label: "Broad Markets" }, { key: "commodities", label: "Commodities" }]} label="Market views" onChange={setMode} />
      <div className="control-deck">
        <label>{mode === "commodities" ? "Commodity lens" : "Business lens"}
          <select onChange={(event) => mode === "commodities" ? setCommodity(event.target.value) : setBusiness(event.target.value)} value={mode === "commodities" ? commodity : business}>
            {(mode === "commodities" ? lenses?.commodities : lenses?.business)?.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.symbol_count}</option>)}
          </select>
        </label>
        <label>Horizon<select onChange={(event) => setHorizon(event.target.value)} value={horizon}>{lenses?.horizons.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</select></label>
        <p>{selectedLens?.description || ""}</p>
      </div>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !rows.length ? <p className="loading-label">Loading market workbook…</p> : null}
      {rows.length ? <>
        <MetricStrip items={[
          { label: "Universe", value: rows.length }, { label: `Advancing · ${selectedHorizon.label}`, value: `${advancing}/${rows.length}` },
          { label: "Average return", value: pct(average), tone: average && average > 0 ? "positive" : "" },
          { label: "Leader", value: sortedRows[0]?.symbol || "—" },
        ]} />
        {mode === "markets" ? (
          <section className="workbook-panel split-visuals">
            <div className="workbook-chart"><DataPlot data={[{ type: "treemap", labels: rows.map((row) => row.symbol), parents: rows.map(() => ""), values: rows.map((row) => numeric(row.volume) || 0), marker: { colors: rows.map((row) => numeric(row.daily_change_pct) || 0), colorscale: "RdYlGn", cmid: 0, colorbar: { title: "1D %" } }, text: rows.map((row) => `${row.symbol}<br>${pct(row.daily_change_pct)}`), textinfo: "text" }]} id={`market-tree-${business}-${horizon}`} layout={{ margin: { l: 8, r: 8, t: 48, b: 8 } }} rowCount={rows.length} title={`${title} · volume and daily move`} /></div>
            <div className="workbook-chart"><DataPlot data={[{ type: "scatter", mode: "markers+text", x: rows.map((row) => numeric(row.momentum_score)), y: rows.map((row) => numeric(row[selectedHorizon.column])), text: rows.map((row) => row.symbol), textposition: "top center", marker: { size: rows.map((row) => Math.max(7, Math.min(26, Math.sqrt(numeric(row.volume) || 0) / 350))), color: rows.map((row) => numeric(row.opportunity_score)), colorscale: "Viridis", showscale: true } }]} id={`market-scatter-${business}-${horizon}`} layout={{ xaxis: { title: "Momentum" }, yaxis: { title: `${selectedHorizon.label} %` }, hovermode: "closest" }} rowCount={rows.length} title="Momentum structure" /></div>
          </section>
        ) : (
          <section className="workbook-panel split-visuals">
            <div className="workbook-chart"><DataPlot data={[{ type: "scatter3d", mode: "markers+text", x: rows.map((row) => numeric(row.return_1m_pct)), y: rows.map((row) => numeric(row.return_3m_pct)), z: rows.map((row) => numeric(row.return_1y_pct)), text: rows.map((row) => row.symbol), marker: { size: 5, color: rows.map((row) => numeric(row.momentum_roc_score)), colorscale: "Plasma", showscale: true } }]} id={`market-3d-${mode}-${title}`} layout={{ scene: { xaxis: { title: "1M %" }, yaxis: { title: "3M %" }, zaxis: { title: "1Y %" } } }} rowCount={rows.length} title={`${title} · return topology`} /></div>
            <div className="workbook-chart"><DataPlot data={[{ type: "heatmap", x: lenses?.horizons.map((item) => item.label), y: sortedRows.map((row) => row.symbol), z: sortedRows.map((row) => horizonColumns.map((column) => numeric(row[column]))), colorscale: "RdBu", zmid: 0, colorbar: { title: "%" } }]} id={`market-heat-${mode}-${title}`} layout={{ margin: { l: 58, r: 24, t: 48, b: 48 } }} rowCount={rows.length} title={`${title} · multi-horizon heatmap`} /></div>
          </section>
        )}
        <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Ranked universe</p><h2>{title}</h2></div><span>{selectedHorizon.label}</span></header>
          <DataTable columns={[
            { key: "symbol", label: "Ticker" }, { key: "company_name", label: "Company" },
            { key: "close", label: "Close", align: "right", render: money },
            { key: "daily_change_pct", label: "1D", align: "right", render: pct },
            { key: selectedHorizon.column as keyof MarketRow & string, label: selectedHorizon.label, align: "right", render: pct },
            { key: "return_3m_pct", label: "3M", align: "right", render: pct },
            { key: "opportunity_score", label: "Score", align: "right", render: (value) => numeric(value)?.toFixed(1) || "—" },
            { key: "direction", label: "Pattern" },
          ]} onRowClick={(row) => router.push(`/stock-investigator?ticker=${encodeURIComponent(row.symbol)}`)} rowKey={(row) => row.symbol} rows={sortedRows} />
        </section>
      </> : null}
    </div>
  );
}
