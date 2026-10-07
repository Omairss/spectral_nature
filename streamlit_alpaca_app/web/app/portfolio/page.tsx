"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { useAuthenticatedSession } from "@/lib/client/session";
import { usePortfolio } from "@/lib/client/portfolio";

type Row = Record<string, unknown> & { asset_class?: string; symbol?: string; description?: string };
function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function money(value: unknown) { const parsed = number(value); return parsed === null ? "—" : parsed.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }); }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${(Math.abs(parsed) <= 1 ? parsed * 100 : parsed).toFixed(1)}%`; }
const periods = ["1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"];

export default function PortfolioPage() {
  const router = useRouter();
  const { session, sessionError } = useAuthenticatedSession("/portfolio");
  const [period, setPeriod] = useState("5Y");
  const [assetClass, setAssetClass] = useState("equity");
  const { portfolio, loading, error } = usePortfolio(period, Boolean(session?.authenticated));
  const holdings = useMemo(() => portfolio?.holdings || [], [portfolio]);
  const visible = useMemo(() => holdings.filter((row) => (row.asset_class || "equity") === assetClass), [assetClass, holdings]);
  const equities = holdings.filter((row) => (row.asset_class || "equity") === "equity");
  const options = holdings.filter((row) => row.asset_class === "option");
  const futures = holdings.filter((row) => row.asset_class === "future");
  const summary = portfolio?.summary || {};
  const analytics = portfolio?.equity_analytics;
  const diagnosticRows = analytics?.positions || [];
  const readyDiagnostics = diagnosticRows.filter((row) => row.history_status === "Ready");
  const chartHoldings = holdings.filter((row) => Math.abs(number(row.market_value) || 0) > 0);
  const exposureIds = ["equity", "option", "future", ...chartHoldings.map((row, index) => `${row.asset_class || "equity"}:${row.symbol || index}`)];
  const exposureLabels = ["Equities", "Options", "Futures", ...chartHoldings.map((row) => String(row.symbol || row.description || "Holding"))];
  const exposureParents = ["", "", "", ...chartHoldings.map((row) => String(row.asset_class || "equity"))];
  const exposureValues = [
    ...["equity", "option", "future"].map((kind) => chartHoldings.filter((row) => (row.asset_class || "equity") === kind).reduce((sum, row) => sum + Math.abs(number(row.market_value) || 0), 0)),
    ...chartHoldings.map((row) => Math.abs(number(row.market_value) || 0)),
  ];

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page wide-workbook">
      <header className="artifact-header workbook-heading">
        <div><p className="breadcrumb">Capital <span>/</span> Portfolio</p><h1>{portfolio?.portfolio.name || "Portfolio"}</h1></div>
        <label className="compact-control">History<select onChange={(event) => setPeriod(event.target.value)} value={period}>{periods.map((value) => <option key={value}>{value}</option>)}</select></label>
      </header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !portfolio ? <p className="loading-label">Loading portfolio…</p> : null}
      {portfolio?.status === "available" ? <>
        <MetricStrip items={[
          { label: "Equity", value: money(summary.equity ?? summary.portfolio_value) },
          { label: "Cash", value: money(summary.cash) },
          { label: "Buying power", value: money(summary.buying_power) },
          { label: "Day P/L", value: money(summary.day_pl), tone: (number(summary.day_pl) || 0) > 0 ? "positive" : "" },
          { label: "Unrealized P/L", value: money(summary.total_unrealized_pl), tone: (number(summary.total_unrealized_pl) || 0) > 0 ? "positive" : "" },
          { label: "Net exposure", value: money(summary.net_exposure) },
        ]} />
        <section className="workbook-panel">
          <WorkbookTabs active={assetClass} items={[
            { key: "equity", label: `Equities · ${equities.length}` }, { key: "option", label: `Options · ${options.length}` }, { key: "future", label: `Futures · ${futures.length}` },
          ]} label="Holding classes" onChange={setAssetClass} />
          <DataTable<Row> columns={assetClass === "equity" ? [
            { key: "account", label: "Account" }, { key: "symbol", label: "Symbol" }, { key: "direction", label: "Side" },
            { key: "qty", label: "Quantity", align: "right" }, { key: "current_price", label: "Price", align: "right", render: money },
            { key: "market_value", label: "Market value", align: "right", render: money }, { key: "change_today", label: "Today", align: "right", render: pct },
            { key: "unrealized_pl", label: "Unrealized", align: "right", render: money }, { key: "unrealized_plpc", label: "Return", align: "right", render: pct },
          ] : assetClass === "option" ? [
            { key: "account", label: "Account" }, { key: "symbol", label: "Underlying" }, { key: "description", label: "Contract" },
            { key: "strategy", label: "Strategy" }, { key: "quantity", label: "Quantity", align: "right" }, { key: "market_value", label: "Market value", align: "right", render: money },
            { key: "delta", label: "Delta", align: "right" }, { key: "theta", label: "Theta", align: "right" }, { key: "implied_volatility", label: "IV", align: "right", render: pct },
          ] : [
            { key: "account", label: "Account" }, { key: "symbol", label: "Symbol" }, { key: "description", label: "Contract" },
            { key: "quantity", label: "Quantity", align: "right" }, { key: "current_price", label: "Price", align: "right", render: money },
            { key: "market_value", label: "Market value", align: "right", render: money }, { key: "unrealized_pl", label: "Unrealized", align: "right", render: money },
          ]} onRowClick={(row) => row.symbol && router.push(`/stock-investigator?ticker=${encodeURIComponent(row.symbol)}`)} rowKey={(row, index) => `${row.asset_class}-${row.account || ""}-${row.symbol || index}-${row.description || ""}`} rows={visible} />
        </section>
        {chartHoldings.length ? <section className="workbook-panel split-visuals">
          <div className="workbook-chart"><DataPlot data={[{ type: "sunburst", ids: exposureIds, labels: exposureLabels, parents: exposureParents, values: exposureValues, branchvalues: "total", marker: { colorscale: "Viridis" } }]} id={`portfolio-exposure-${period}`} layout={{ margin: { l: 8, r: 8, t: 48, b: 8 } }} rowCount={chartHoldings.length} title="Portfolio exposure" /></div>
          <div className="workbook-chart"><DataPlot data={[{ type: "bar", orientation: "h", y: chartHoldings.map((row) => String(row.symbol || row.description || "Holding")), x: chartHoldings.map((row) => number(row.unrealized_pl)), marker: { color: chartHoldings.map((row) => number(row.unrealized_pl)), colorscale: "RdYlGn", cmid: 0 } }]} id={`portfolio-pnl-${period}`} layout={{ xaxis: { title: "Unrealized P/L", tickprefix: "$" }, margin: { l: 72, r: 24, t: 48, b: 48 } }} rowCount={chartHoldings.length} title="Unrealized P/L contribution" /></div>
        </section> : null}
        {portfolio.timeseries.length ? <section className="workbook-panel"><div className="workbook-chart"><DataPlot data={Object.keys(portfolio.timeseries[0] || {}).filter((key) => key !== "timestamp").map((key) => ({ type: "scatter", mode: "lines", name: key, x: portfolio.timeseries.map((row) => row.timestamp), y: portfolio.timeseries.map((row) => row[key]), line: { width: key === "portfolio" ? 2.8 : 1.2 } }))} id={`portfolio-history-${period}`} layout={{ hovermode: "x unified", yaxis: { title: "Value" } }} rowCount={portfolio.timeseries.length} title={`Portfolio history · ${period}`} /></div></section> : null}
        {readyDiagnostics.length ? <section className="workbook-panel">
          <header className="panel-header"><div><p className="section-label">Risk system</p><h2>Equity diagnostics</h2></div><span>{period}</span></header>
          <MetricStrip items={[
            { label: "History coverage", value: pct(analytics?.stats.history_coverage) },
            { label: "Portfolio volatility", value: pct(analytics?.stats.portfolio_volatility) },
            { label: "SPY beta", value: number(analytics?.stats.portfolio_beta_vs_spy)?.toFixed(2) || "—" },
            { label: "Effective positions", value: number(analytics?.stats.effective_positions)?.toFixed(1) || "—" },
            { label: "Independent risk bets", value: number(analytics?.stats.effective_risk_bets)?.toFixed(1) || "—" },
          ]} />
          <div className="split-visuals">
            <div className="workbook-chart"><DataPlot data={[{ type: "scatter", mode: "markers+text", x: readyDiagnostics.map((row) => number(row.annualized_volatility)), y: readyDiagnostics.map((row) => number(row.period_return)), text: readyDiagnostics.map((row) => row.symbol), textposition: "top center", marker: { size: readyDiagnostics.map((row) => Math.max(8, Math.min(42, Math.sqrt(number(row.gross_market_value) || 0) / 18))), color: readyDiagnostics.map((row) => number(row.risk_share)), colorscale: "RdBu", cmid: 0, showscale: true } }]} id={`portfolio-risk-${period}`} layout={{ xaxis: { title: "Annualized volatility", tickformat: ".0%" }, yaxis: { title: `${period} return`, tickformat: ".0%" } }} rowCount={readyDiagnostics.length} title="Risk and return" /></div>
            {analytics?.correlation.labels.length ? <div className="workbook-chart"><DataPlot data={[{ type: "heatmap", x: analytics.correlation.labels, y: analytics.correlation.labels, z: analytics.correlation.values, colorscale: "RdBu", zmin: -1, zmax: 1, zmid: 0 }]} id={`portfolio-correlation-${period}`} layout={{ margin: { l: 64, r: 24, t: 48, b: 64 } }} rowCount={analytics.correlation.labels.length} title="Return correlation" /></div> : null}
          </div>
          <DataTable columns={[
            { key: "symbol", label: "Symbol" }, { key: "allocation", label: "Allocation", align: "right", render: pct },
            { key: "period_return", label: `${period} return`, align: "right", render: pct }, { key: "annualized_volatility", label: "Volatility", align: "right", render: pct },
            { key: "max_drawdown", label: "Max drawdown", align: "right", render: pct }, { key: "beta_vs_spy", label: "SPY beta", align: "right", render: (value) => number(value)?.toFixed(2) || "—" },
            { key: "momentum_regime", label: "Regime" },
          ]} onRowClick={(row) => row.symbol && router.push(`/stock-investigator?ticker=${encodeURIComponent(String(row.symbol))}`)} rowKey={(row, index) => String(row.symbol || index)} rows={readyDiagnostics} />
        </section> : null}
        {portfolio.opportunity_sets.length ? <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Lists and screeners</p><h2>Tracked opportunity sets</h2></div><span>{portfolio.opportunity_sets.length}</span></header>
          <DataTable columns={[{ key: "name", label: "Group" }, { key: "kind", label: "Type" }, { key: "member_count", label: "Members", align: "right" }, { key: "analytics_status", label: "Analytics" }]} rowKey={(row, index) => String(row.set_key || index)} rows={portfolio.opportunity_sets} />
        </section> : null}
      </> : null}
    </div>
  );
}
