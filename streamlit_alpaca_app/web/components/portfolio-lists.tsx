"use client";

import { useEffect, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip } from "@/components/workbook";
import { responseError } from "@/lib/client/api";

type Row = Record<string, unknown>;
type Analytics = { status: string; series: { group_key: string; points: { date: string; value: number }[] }[]; rankings: Row[]; contributions: Row[]; sets: Row[]; correlation: { keys: string[]; labels: string[]; values: (number | null)[][] }; return_period: string; risk_period: string };
const value = (row: Row, key: string) => row[key] == null || !Number.isFinite(Number(row[key])) ? null : Number(row[key]);
const percent = (v: unknown) => v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

function contributorFlow(data: Analytics, lens: string, depth: number) {
  const [metric, category] = ({ "Return drivers": ["return_contribution", "direction"], "Risk concentration": ["risk_contribution", "risk_direction"], "Momentum regimes": ["regime_intensity", "regime"] })[lens]!;
  const best = data.rankings.slice(0, depth); const bestKeys = new Set(best.map(row => row.group_key));
  const worst = data.rankings.filter(row => !bestKeys.has(row.group_key)).slice(-depth).reverse();
  const labels: string[] = [], colors: string[] = [], sources: number[] = [], targets: number[] = [], magnitudes: number[] = [], signed: number[] = [];
  const node = (label: string, sign: number) => { labels.push(label); colors.push(sign >= 0 ? "#52d6c7" : "#f08c73"); return labels.length - 1; };
  for (const group of [...best, ...worst]) {
    const members = data.contributions.filter(row => row.group_key === group.group_key && value(row, metric) != null && value(row, metric) !== 0);
    if (!members.length) continue;
    const root = node(`${bestKeys.has(group.group_key) ? "Best" : "Worst"} · ${group.group} · ${percent(group.return)}`, value(group, "return") || 0);
    const categories = Array.from(new Set(members.map(row => String(row[category])))).sort();
    for (const name of categories) {
      const rows = members.filter(row => row[category] === name).sort((a, b) => Math.abs(value(b, metric) || 0) - Math.abs(value(a, metric) || 0));
      const total = rows.reduce((total, row) => total + (value(row, metric) || 0), 0);
      const middle = node(name, total);
      sources.push(root); targets.push(middle); magnitudes.push(rows.reduce((total, row) => total + Math.abs(value(row, metric) || 0), 0)); signed.push(total);
      const leaves = rows.slice(0, 3).map(row => ({ symbol: String(row.symbol), signed: value(row, metric) || 0, magnitude: Math.abs(value(row, metric) || 0) }));
      if (rows.length > 3) leaves.push({ symbol: `Other (${rows.length - 3})`, signed: rows.slice(3).reduce((total, row) => total + (value(row, metric) || 0), 0), magnitude: rows.slice(3).reduce((total, row) => total + Math.abs(value(row, metric) || 0), 0) });
      for (const leaf of leaves) {
        const terminal = node(leaf.symbol, leaf.signed);
        sources.push(middle); targets.push(terminal); magnitudes.push(leaf.magnitude); signed.push(leaf.signed);
      }
    }
  }
  return { type: "sankey", arrangement: "snap", node: { label: labels, color: colors, pad: 16, thickness: 15 }, link: { source: sources, target: targets, value: magnitudes, customdata: signed, color: signed.map(v => v >= 0 ? "rgba(82,214,199,0.3)" : "rgba(240,140,115,0.3)"), hovertemplate: lens === "Momentum regimes" ? "%{source.label} → %{target.label}<br>%{customdata:.3f}<extra></extra>" : "%{source.label} → %{target.label}<br>%{customdata:.2%}<extra></extra>" } };
}

export function PortfolioLists({ period, initialData }: { period: string; initialData?: Analytics }) {
  const [data, setData] = useState<Analytics | null>(initialData || null), [error, setError] = useState("");
  const [selected, setSelected] = useState<string[]>(initialData ? Array.from(new Set([...initialData.rankings.slice(0, 3), ...initialData.rankings.slice(-3)].map(row => String(row.group_key)))) : []), [lens, setLens] = useState("Return drivers"), [depth, setDepth] = useState(2);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (initialData) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setError("");
      try {
        const response = await fetch(`/api/portfolio/opportunities?period=${encodeURIComponent(period)}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(await responseError(response, "List analytics are unavailable."));
        const result = await response.json() as Analytics;
        if (!controller.signal.aborted) {
          setData(result);
          setSelected(Array.from(new Set([...result.rankings.slice(0, 3), ...result.rankings.slice(-3)].map(row => String(row.group_key)))));
        }
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "List analytics are unavailable."); }
    }, 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [period, retry, initialData]);
  if (error) return <section className="workbook-panel"><p role="alert">{error}</p><button onClick={() => setRetry(retry + 1)}>Try again</button></section>;
  if (!data) return <p className="loading-label">Loading list histories…</p>;
  if (data.status !== "available") return <p>List histories unavailable</p>;
  const groups = data.rankings.filter(row => selected.includes(String(row.group_key)));
  const histories = groups.map(group => {
    const points = data.series.find(row => row.group_key === group.group_key)?.points || [];
    return { type: "scatter", mode: "lines", name: String(group.group), x: points.map(point => point.date), y: points.map(point => point.value) };
  });
  const indices = data.correlation.keys.map((key, index) => selected.includes(key) ? index : -1).filter(index => index >= 0);
  const labels = indices.map(index => data.correlation.labels[index]);
  return <section className="workbook-panel">
    <header className="panel-header"><h2>Lists and screeners</h2></header>
    <details><summary>Compare groups · {selected.length}</summary><div className="list-comparison-picker">
      {data.rankings.map(row => <label key={String(row.group_key)} style={{ display: "flex", gap: "0.5rem", marginBlock: "0.4rem" }}><input type="checkbox" checked={selected.includes(String(row.group_key))} disabled={!selected.includes(String(row.group_key)) && selected.length >= 6} onChange={event => setSelected(event.target.checked ? [...selected, String(row.group_key)] : selected.filter(key => key !== row.group_key))} />{String(row.group)} · {percent(row.return)}</label>)}
    </div></details>
    {groups.length ? <DataPlot data={histories} id={`list-history-${period}`} layout={{ hovermode: "x unified", yaxis: { title: "Equal-weight index · starts at 100" } }} rowCount={data.series.length} title={`Equal-weight list performance · ${period}`} /> : null}
    <div className="split-visuals">
      <DataPlot data={[{ type: "scatter", mode: "markers", text: groups.map(row => row.group), x: groups.map(row => value(row, "volatility")), y: groups.map(row => value(row, "return")), marker: { size: groups.map(row => Math.max(10, Math.min(42, Math.sqrt(value(row, "members") || 1) * 3))), color: groups.map(row => value(row, "breadth")), colorscale: "RdYlGn", cmin: 0, cmax: 1 }, hovertemplate: "%{text}<br>Return %{y:.1%}<br>Volatility %{x:.1%}<extra></extra>" }]} id={`list-risk-${period}`} layout={{ xaxis: { title: `${data.risk_period} annualized volatility`, tickformat: ".0%" }, yaxis: { title: `${period} return`, tickformat: ".0%" } }} rowCount={groups.length} title="Group risk and return" />
      {labels.length ? <DataPlot data={[{ type: "heatmap", x: labels, y: labels, z: indices.map(i => indices.map(j => data.correlation.values[i][j])), colorscale: "RdBu", zmin: -1, zmax: 1, zmid: 0 }]} id={`list-correlation-${period}`} layout={{ xaxis: { automargin: true }, yaxis: { automargin: true }, margin: { l: 70, r: 30, t: 60, b: 90 } }} rowCount={labels.length} title={`Group-return correlation · ${data.risk_period}`} /> : null}
    </div>
    <div className="list-flow-controls"><label>Flow lens<select value={lens} onChange={event => setLens(event.target.value)}>{["Return drivers", "Risk concentration", "Momentum regimes"].map(lens => <option key={lens}>{lens}</option>)}</select></label>
      <label>Groups per side<select value={depth} onChange={event => setDepth(Number(event.target.value))}>{Array.from({ length: Math.min(5, Math.max(1, Math.floor(data.rankings.length / 2))) }, (_, index) => <option key={index}>{index + 1}</option>)}</select></label></div>
    <DataPlot data={[contributorFlow(data, lens, depth)]} id={`list-flow-${period}-${lens}-${depth}`} layout={{ height: 650, margin: { l: 10, r: 10, t: 55, b: 20 } }} rowCount={data.contributions.length} title={`Best/worst by ${period} return · ${lens === "Return drivers" ? period : data.risk_period} ${lens.toLowerCase()}`} />
    <details><summary>Group diagnostics</summary><MetricStrip items={[{ label: "Return horizon", value: period }, { label: "Risk estimation", value: data.risk_period }]} /><DataTable columns={[
      { key: "group", label: "Group" }, { key: "return", label: "Return", render: percent }, { key: "history_coverage", label: "History coverage", render: percent }, { key: "start_at", label: "History start" }, { key: "end_at", label: "History end" }, { key: "breadth", label: "Breadth", render: percent }, { key: "volatility", label: "Volatility", render: percent }, { key: "max_drawdown", label: "Drawdown", render: percent }, { key: "tail_loss_5pct", label: "Daily tail loss", render: percent }, { key: "dispersion", label: "Dispersion", render: percent }, { key: "top_three_concentration", label: "Top-three contribution", render: percent }, { key: "effective_risk_bets", label: "Risk bets" }, { key: "diversification_ratio", label: "Diversification" }, { key: "regime", label: "Regime" }, { key: "members", label: "Members" },
    ]} rowKey={row => String(row.group_key)} rows={data.rankings} /></details>
    <details><summary>All lists and screeners</summary><DataTable columns={[{ key: "name", label: "Group" }, { key: "kind", label: "Type" }, { key: "member_count", label: "Members" }, { key: "analytics_status", label: "History" }]} rowKey={row => String(row.set_key)} rows={data.sets} /></details>
  </section>;
}
