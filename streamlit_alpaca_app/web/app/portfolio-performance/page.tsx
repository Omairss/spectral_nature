"use client";

import { useMemo, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip } from "@/components/workbook";
import { usePortfolio } from "@/lib/client/portfolio";
import { useAuthenticatedSession } from "@/lib/client/session";

function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function money(value: unknown) { const parsed = number(value); return parsed === null ? "—" : parsed.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }); }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${parsed > 0 ? "+" : ""}${(Math.abs(parsed) <= 1 ? parsed * 100 : parsed).toFixed(2)}%`; }
const periods = ["1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"];

function drawdownSeries(rows: Array<Record<string, unknown>>) {
  let peak = -Infinity;
  return rows.map((row) => {
    const value = number(row.portfolio);
    if (value === null) return null;
    peak = Math.max(peak, value);
    return peak > 0 ? value / peak - 1 : null;
  });
}

function monthlyReturns(rows: Array<Record<string, unknown>>) {
  const byMonth = new Map<string, number>();
  rows.forEach((row) => {
    const timestamp = new Date(String(row.timestamp || ""));
    const value = number(row.portfolio);
    if (!Number.isNaN(timestamp.getTime()) && value !== null) {
      byMonth.set(`${timestamp.getUTCFullYear()}-${String(timestamp.getUTCMonth() + 1).padStart(2, "0")}`, value);
    }
  });
  const entries = Array.from(byMonth.entries()).sort();
  return entries.map(([key, value], index) => ({
    key,
    year: key.slice(0, 4),
    month: Number(key.slice(5)),
    value: index > 0 && entries[index - 1][1] ? value / entries[index - 1][1] - 1 : null,
  })).filter((row) => row.value !== null);
}

export default function PortfolioPerformancePage() {
  const { session, sessionError } = useAuthenticatedSession("/portfolio-performance");
  const [period, setPeriod] = useState("1Y");
  const { portfolio, loading, error } = usePortfolio(period, Boolean(session?.authenticated));
  const rows = useMemo(() => portfolio?.timeseries || [], [portfolio]);
  const metrics = portfolio?.performance.metrics || {};
  const normalized = useMemo<Array<Record<string, unknown>>>(() => {
    if (!rows.length) return [];
    const columns = Object.keys(rows[0]).filter((key) => key !== "timestamp");
    const base = Object.fromEntries(columns.map((key) => [key, number(rows.find((row) => number(row[key]) !== null)?.[key])]));
    return rows.map((row) => ({ timestamp: row.timestamp, ...Object.fromEntries(columns.map((key) => [key, base[key] ? (number(row[key])! / base[key]!) * 100 : null])) })) as Array<Record<string, unknown>>;
  }, [rows]);
  const underwater = useMemo(() => drawdownSeries(rows), [rows]);
  const monthly = useMemo(() => monthlyReturns(rows), [rows]);
  const years = Array.from(new Set(monthly.map((row) => row.year)));

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page wide-workbook">
      <header className="artifact-header workbook-heading"><div><p className="breadcrumb">Capital <span>/</span> Performance</p><h1>Portfolio Performance</h1></div><label className="compact-control">Period<select onChange={(event) => setPeriod(event.target.value)} value={period}>{periods.map((value) => <option key={value}>{value}</option>)}</select></label></header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !portfolio ? <p className="loading-label">Loading performance…</p> : null}
      {rows.length ? <>
        <MetricStrip items={[
          { label: "Start", value: money(metrics.start_value) }, { label: "Current", value: money(metrics.current_value) },
          { label: "Change", value: money(metrics.change_value), tone: (number(metrics.change_value) || 0) > 0 ? "positive" : "" },
          { label: "Return", value: pct(metrics.return), tone: (number(metrics.return) || 0) > 0 ? "positive" : "" },
          { label: "Max drawdown", value: pct(metrics.max_drawdown) }, { label: "Annualized volatility", value: pct(metrics.annualized_volatility) },
        ]} />
        <section className="workbook-panel split-visuals">
          <div className="workbook-chart"><DataPlot data={[{ type: "scatter", mode: "lines", x: rows.map((row) => row.timestamp), y: rows.map((row) => row.portfolio), line: { color: "#6cb8ff", width: 2.5 }, fill: "tozeroy", fillcolor: "rgba(108,184,255,.08)" }]} id={`performance-value-${period}`} layout={{ hovermode: "x unified", yaxis: { tickprefix: "$" } }} rowCount={rows.length} title={`Portfolio value · ${period}`} /></div>
          <div className="workbook-chart"><DataPlot data={Object.keys(normalized[0] || {}).filter((key) => key !== "timestamp").map((key) => ({ type: "scatter", mode: "lines", name: key, x: normalized.map((row) => row.timestamp), y: normalized.map((row) => row[key]), line: { width: key === "portfolio" ? 2.8 : 1.2 } }))} id={`performance-benchmarks-${period}`} layout={{ hovermode: "x unified", yaxis: { title: "Indexed to 100" } }} rowCount={normalized.length} title={`Portfolio vs benchmarks · ${period}`} /></div>
        </section>
        <section className="workbook-panel split-visuals">
          <div className="workbook-chart"><DataPlot data={[{ type: "scatter", mode: "lines", x: rows.map((row) => row.timestamp), y: underwater, line: { color: "#f08b72", width: 2 }, fill: "tozeroy", fillcolor: "rgba(240,139,114,.12)" }]} id={`performance-drawdown-${period}`} layout={{ yaxis: { tickformat: ".0%" }, hovermode: "x unified" }} rowCount={rows.length} title={`Underwater curve · ${period}`} /></div>
          <div className="workbook-chart"><DataPlot data={[{ type: "heatmap", x: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], y: years, z: years.map((year) => Array.from({ length: 12 }, (_, month) => monthly.find((row) => row.year === year && row.month === month + 1)?.value ?? null)), colorscale: "RdYlGn", zmid: 0, colorbar: { tickformat: ".0%" } }]} id={`performance-monthly-${period}`} layout={{ margin: { l: 52, r: 24, t: 48, b: 48 } }} rowCount={monthly.length} title="Monthly returns" /></div>
        </section>
        {portfolio?.performance.table.length ? <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Statistics</p><h2>Annual performance</h2></div></header>
          <DataTable columns={[{ key: "entity", label: "Entity" }, { key: "year", label: "Year" }, { key: "annual_return", label: "Return", align: "right", render: pct }, { key: "sharpe_ratio", label: "Sharpe", align: "right", render: (value) => number(value)?.toFixed(2) || "—" }, { key: "beta_vs_spy", label: "Beta", align: "right", render: (value) => number(value)?.toFixed(2) || "—" }, { key: "alpha_vs_spy", label: "Alpha", align: "right", render: pct }, { key: "max_drawdown", label: "Max drawdown", align: "right", render: pct }]} rowKey={(row, index) => `${row.entity}-${row.year}-${index}`} rows={portfolio.performance.table} />
        </section> : null}
      </> : null}
    </div>
  );
}
