"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { fetchDataset } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";

type MacroSummary = Record<string, unknown> & {
  category: string;
  series_id: string;
  indicator: string;
  units_short: string;
  frequency_short: string;
  latest_date: string;
  latest_value: number | null;
  prev_delta: number | null;
  yoy_delta: number | null;
  yoy_pct: number | null;
  latest_rate_of_change: number | null;
  rate_of_change_kind: "delta" | "percent_change" | "";
  rate_of_change_label: string;
};
type Observation = Record<string, unknown> & {
  series_id: string;
  date: string;
  value: number | null;
  rate_of_change: number | null;
  release_id: string;
};
type Correlation = {
  category: string;
  left_series_id: string;
  right_series_id: string;
  correlation: number | null;
  observations: number;
};
type SeriesIndex = Record<string, unknown> & {
  series_id: string;
  title: string;
  frequency: string;
  frequency_short: string;
  units: string;
  units_short: string;
  release_name: string;
  notes: string;
};
type MacroPayload = {
  summary: MacroSummary[];
  observations: Observation[];
  correlations: Correlation[];
  series_index: SeriesIndex[];
  category_blurbs: Record<string, string>;
};

function number(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatValue(value: unknown, units = "") {
  const parsed = number(value);
  if (parsed === null) return "—";
  const digits = Math.abs(parsed) >= 100 ? 1 : 2;
  const base = parsed.toLocaleString(undefined, { maximumFractionDigits: digits });
  return units === "%" || units.toLowerCase().includes("percent") ? `${base}%` : base;
}

function formatDelta(value: unknown, units = "") {
  const parsed = number(value);
  if (parsed === null) return "—";
  if (units === "%" || units.toLowerCase().includes("percent")) {
    return `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)} pp`;
  }
  return `${parsed > 0 ? "+" : ""}${formatValue(parsed, units)}`;
}

function formatRate(value: unknown, kind: MacroSummary["rate_of_change_kind"]) {
  const parsed = number(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)}${kind === "percent_change" ? "%" : " pp"}`;
}

function formatDate(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "—";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

function tone(value: unknown) {
  const parsed = number(value);
  if (parsed === null || parsed === 0) return "";
  return parsed > 0 ? "positive" : "negative";
}

function MacroChart({
  observations,
  row,
  selected,
  years,
  onSelect,
}: {
  observations: Observation[];
  row: MacroSummary;
  selected: boolean;
  years: number;
  onSelect: () => void;
}) {
  return (
    <article className={`macro-chart-card${selected ? " selected" : ""}`}>
      <button className="macro-chart-heading" onClick={onSelect} type="button">
        <span><strong>{row.indicator}</strong><small>{row.series_id} · {row.frequency_short}</small></span>
        <span><strong>{formatValue(row.latest_value, row.units_short)}</strong><small className={tone(row.latest_rate_of_change)}>{formatRate(row.latest_rate_of_change, row.rate_of_change_kind)}</small></span>
      </button>
      <div className="macro-chart-legend" aria-hidden="true"><span>Level</span><span>{row.rate_of_change_kind === "percent_change" ? "% change" : "Change"}</span></div>
      <div className="macro-mini-chart">
        <DataPlot
          data={[
            {
              type: "scatter",
              mode: "lines",
              name: row.indicator,
              x: observations.map((item) => item.date),
              y: observations.map((item) => item.value),
              line: { color: "#52d6c7", width: 2 },
              hovertemplate: "%{x|%Y-%m-%d}<br>Level %{y:,.2f}<extra></extra>",
            },
            {
              type: "scatter",
              mode: "lines",
              name: row.rate_of_change_label || "Rate of change",
              x: observations.map((item) => item.date),
              y: observations.map((item) => item.rate_of_change),
              yaxis: "y2",
              line: { color: "#a991f7", width: 1.25, dash: "dot" },
              hovertemplate: "%{x|%Y-%m-%d}<br>Change %{y:,.2f}<extra></extra>",
              connectgaps: false,
            },
          ]}
          id={`macro-${row.series_id}-${years}`}
          displayModeBar={false}
          layout={{
            title: { text: "" },
            showlegend: false,
            hovermode: "x unified",
            margin: { l: 38, r: 38, t: 8, b: 28 },
            xaxis: { title: "", showgrid: false, zeroline: false },
            yaxis: { title: "", showgrid: true, gridcolor: "rgba(91,105,120,.13)", zeroline: false },
            yaxis2: { title: "", overlaying: "y", side: "right", showgrid: false, zeroline: true, zerolinecolor: "rgba(169,145,247,.2)" },
          }}
          rowCount={observations.length}
          title={row.indicator}
        />
      </div>
    </article>
  );
}

export default function BroadEconomyPage() {
  const { session, sessionError } = useAuthenticatedSession("/broad-economy");
  const [years, setYears] = useState(10);
  const [payload, setPayload] = useState<MacroPayload | null>(null);
  const [activeCategory, setActiveCategory] = useState("Money Supply");
  const [selectedSeries, setSelectedSeries] = useState("M2SL");
  const [search, setSearch] = useState("");
  const [release, setRelease] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (lookback: number) => {
    await Promise.resolve();
    setLoading(true);
    setError("");
    try {
      const result = await fetchDataset<MacroPayload>("fred_dashboard", { years: lookback });
      setPayload(result.payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Macro data could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(years); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session, years]);

  const categories = useMemo(() => Array.from(new Set((payload?.summary || []).map((row) => row.category).filter(Boolean))), [payload]);
  const resolvedCategory = categories.includes(activeCategory) ? activeCategory : categories[0] || activeCategory;
  const categoryRows = useMemo(() => (payload?.summary || []).filter((row) => row.category === resolvedCategory), [payload, resolvedCategory]);
  const observationsBySeries = useMemo(() => {
    const grouped = new Map<string, Observation[]>();
    for (const row of payload?.observations || []) {
      if (number(row.value) === null) continue;
      const rows = grouped.get(row.series_id) || [];
      rows.push(row);
      grouped.set(row.series_id, rows);
    }
    return grouped;
  }, [payload]);
  const releases = useMemo(() => Array.from(new Set((payload?.series_index || []).map((row) => row.release_name).filter(Boolean))).sort(), [payload]);
  const seriesRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (payload?.series_index || []).filter((row) => {
      if (release && row.release_name !== release) return false;
      if (!query) return true;
      return `${row.series_id} ${row.title} ${row.notes}`.toLowerCase().includes(query);
    }).slice(0, 250);
  }, [payload, release, search]);
  const activeSummary = (payload?.summary || []).find((row) => row.series_id === selectedSeries) || categoryRows[0];
  const activeId = activeSummary?.series_id || selectedSeries;
  const latestCategoryUpdate = categoryRows.map((row) => row.latest_date).filter(Boolean).sort().at(-1) || "";
  const correlationRows = useMemo(
    () => (payload?.correlations || []).filter((row) => row.category === resolvedCategory),
    [payload, resolvedCategory],
  );
  const correlationLookup = useMemo(
    () => new Map(correlationRows.map((row) => [`${row.left_series_id}::${row.right_series_id}`, row])),
    [correlationRows],
  );
  const correlationLabels = categoryRows.map((row) => row.indicator);
  const correlationIds = categoryRows.map((row) => row.series_id);
  const correlationMatrix = correlationIds.map((left) => correlationIds.map((right) => correlationLookup.get(`${left}::${right}`)?.correlation ?? null));
  const correlationCounts = correlationIds.map((left) => correlationIds.map((right) => correlationLookup.get(`${left}::${right}`)?.observations ?? 0));

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;

  return (
    <div className="page-shell workbook-page macro-workspace">
      <header className="artifact-header workbook-heading">
        <div><p className="breadcrumb">Research <span>/</span> Macro</p><h1>Broad Economy</h1></div>
        <label className="compact-control">Lookback
          <select onChange={(event) => setYears(Number(event.target.value))} value={years}>
            {[3, 5, 10, 15, 20].map((value) => <option key={value} value={value}>{value} years</option>)}
          </select>
        </label>
      </header>
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {loading && !payload ? <p className="loading-label">Loading macro workbook…</p> : null}
      {payload ? <>
        <MetricStrip items={[
          { label: "Indicators", value: payload.summary.length },
          { label: "Categories", value: categories.length },
          { label: "Observations", value: payload.observations.length.toLocaleString() },
          { label: "Latest update", value: formatDate(latestCategoryUpdate) },
        ]} />
        <section className="workbook-panel macro-dashboard">
          <WorkbookTabs active={resolvedCategory} items={categories.map((key) => ({ key, label: key }))} label="Macro categories" onChange={(key) => { setActiveCategory(key); const first = payload.summary.find((row) => row.category === key); if (first) setSelectedSeries(first.series_id); }} />
          <div className="panel-intro"><div><p className="section-label">{resolvedCategory}</p><h2>{activeSummary?.indicator || resolvedCategory}</h2></div><p>{payload.category_blurbs?.[resolvedCategory] || ""}</p></div>
          {activeSummary ? <MetricStrip items={[
            { label: "Latest", value: formatValue(activeSummary.latest_value, activeSummary.units_short) },
            { label: "Latest change", value: formatRate(activeSummary.latest_rate_of_change, activeSummary.rate_of_change_kind), tone: tone(activeSummary.latest_rate_of_change) },
            { label: "YoY level change", value: formatDelta(activeSummary.yoy_delta, activeSummary.units_short), tone: tone(activeSummary.yoy_delta) },
            { label: "Last observation", value: formatDate(activeSummary.latest_date) },
          ]} /> : null}
          <div className="macro-chart-grid">
            {categoryRows.map((row) => (
              <MacroChart
                key={row.series_id}
                observations={observationsBySeries.get(row.series_id) || []}
                onSelect={() => setSelectedSeries(row.series_id)}
                row={row}
                selected={row.series_id === activeId}
                years={years}
              />
            ))}
          </div>
        </section>

        {correlationRows.length ? <section className="workbook-panel macro-correlation-panel">
          <header className="panel-header"><div><p className="section-label">Co-movement</p><h2>{resolvedCategory} change correlation</h2></div><span>Monthly aligned · {years} years</span></header>
          <div className={`macro-heatmap ${categoryRows.length <= 4 ? "small" : categoryRows.length <= 6 ? "medium" : "large"}`}>
            <DataPlot
              data={[{
                type: "heatmap",
                x: correlationLabels,
                y: correlationLabels,
                z: correlationMatrix,
                customdata: correlationCounts,
                zmin: -1,
                zmax: 1,
                zmid: 0,
                colorscale: [[0, "#884f61"], [0.5, "#111820"], [1, "#3e9189"]],
                colorbar: { title: "r", thickness: 10, len: 0.72 },
                texttemplate: categoryRows.length <= 6 ? "%{z:.2f}" : "",
                textfont: { color: "#d6dee6", size: 10 },
                hovertemplate: "%{y} × %{x}<br>Correlation %{z:.2f}<br>%{customdata} aligned months<extra></extra>",
                hoverongaps: false,
              }]}
              id={`macro-correlation-${resolvedCategory}-${years}`}
              displayModeBar={false}
              layout={{
                title: { text: "" },
                margin: { l: 130, r: 44, t: 18, b: 120 },
                xaxis: { tickangle: -38, automargin: true, side: "bottom" },
                yaxis: { automargin: true, autorange: "reversed" },
              }}
              rowCount={correlationRows.length}
              title={`${resolvedCategory} change correlation`}
            />
          </div>
        </section> : null}

        <section className="workbook-panel">
          <header className="panel-header"><div><p className="section-label">Latest readings</p><h2>{resolvedCategory}</h2></div><span>{categoryRows.length} indicators</span></header>
          <DataTable columns={[
            { key: "indicator", label: "Indicator" },
            { key: "latest_value", label: "Latest", align: "right", render: (value, row) => formatValue(value, row.units_short) },
            { key: "latest_rate_of_change", label: "Rate of change", align: "right", render: (value, row) => formatRate(value, row.rate_of_change_kind as MacroSummary["rate_of_change_kind"]) },
            { key: "yoy_delta", label: "YoY level change", align: "right", render: (value, row) => formatDelta(value, row.units_short) },
            { key: "latest_date", label: "Observed", render: formatDate },
          ]} onRowClick={(row) => setSelectedSeries(row.series_id)} rowKey={(row) => row.series_id} rows={categoryRows} selectedKey={activeId} />
        </section>

        <section className="workbook-panel">
          <header className="panel-header"><div><p className="section-label">Series index</p><h2>Explore loaded indicators</h2></div><span>{seriesRows.length} shown</span></header>
          <div className="filter-row">
            <input aria-label="Search macro series" onChange={(event) => setSearch(event.target.value)} placeholder="Search series" value={search} />
            <select aria-label="Filter by release" onChange={(event) => setRelease(event.target.value)} value={release}><option value="">All releases</option>{releases.map((value) => <option key={value} value={value}>{value}</option>)}</select>
          </div>
          <DataTable columns={[
            { key: "series_id", label: "Series" }, { key: "title", label: "Title" },
            { key: "release_name", label: "Release" }, { key: "frequency", label: "Frequency" }, { key: "units", label: "Units" },
          ]} onRowClick={(row) => setSelectedSeries(row.series_id)} rowKey={(row) => row.series_id} rows={seriesRows} selectedKey={activeId} />
        </section>
      </> : null}
    </div>
  );
}
