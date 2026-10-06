"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { fetchDataset } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";

type MacroSummary = Record<string, unknown> & {
  category: string; series_id: string; indicator: string; units_short: string;
  frequency_short: string; latest_date: string; latest_value: number | null;
  prev_delta: number | null; yoy_delta: number | null; yoy_pct: number | null;
};
type Observation = Record<string, unknown> & { series_id: string; date: string; value: number | null; release_id: string };
type SeriesIndex = Record<string, unknown> & {
  series_id: string; title: string; frequency: string; frequency_short: string;
  units: string; units_short: string; release_name: string; notes: string;
};
type MacroPayload = {
  summary: MacroSummary[];
  observations: Observation[];
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
  return `${parsed > 0 ? "+" : ""}${formatValue(parsed, units)}`;
}

function formatDate(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "—";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
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
    setLoading(true); setError("");
    try {
      const result = await fetchDataset<MacroPayload>("fred_dashboard", { years: lookback });
      setPayload(result.payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Macro data could not be loaded.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(years); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session, years]);

  const categories = useMemo(() => Array.from(new Set((payload?.summary || []).map((row) => row.category).filter(Boolean))), [payload]);
  const resolvedCategory = categories.includes(activeCategory) ? activeCategory : categories[0] || activeCategory;
  const categoryRows = useMemo(() => (payload?.summary || []).filter((row) => row.category === resolvedCategory), [payload, resolvedCategory]);
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
  const observations = useMemo(() => (payload?.observations || []).filter((row) => row.series_id === activeId && number(row.value) !== null), [activeId, payload]);
  const latestCategoryUpdate = categoryRows.map((row) => row.latest_date).filter(Boolean).sort().at(-1) || "";

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;

  return (
    <div className="page-shell workbook-page">
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
        <section className="workbook-panel">
          <WorkbookTabs active={resolvedCategory} items={categories.map((key) => ({ key, label: key }))} label="Macro categories" onChange={(key) => { setActiveCategory(key); const first = payload.summary.find((row) => row.category === key); if (first) setSelectedSeries(first.series_id); }} />
          <div className="panel-intro"><div><p className="section-label">{resolvedCategory}</p><h2>{activeSummary?.indicator || resolvedCategory}</h2></div><p>{payload.category_blurbs?.[resolvedCategory] || ""}</p></div>
          {activeSummary ? <MetricStrip items={[
            { label: "Latest", value: formatValue(activeSummary.latest_value, activeSummary.units_short) },
            { label: "Obs-to-obs", value: formatDelta(activeSummary.prev_delta, activeSummary.units_short), tone: number(activeSummary.prev_delta) && number(activeSummary.prev_delta)! > 0 ? "positive" : "" },
            { label: "Year over year", value: formatDelta(activeSummary.yoy_delta, activeSummary.units_short) },
            { label: "Last observation", value: formatDate(activeSummary.latest_date) },
          ]} /> : null}
          <div className="workbook-chart tall-chart">
            <DataPlot data={[{ type: "scatter", mode: "lines", name: activeSummary?.indicator || activeId, x: observations.map((row) => row.date), y: observations.map((row) => row.value), line: { color: "#52d6c7", width: 2 }, fill: "tozeroy", fillcolor: "rgba(82,214,199,.06)" }]} id={`macro-${activeId}-${years}`} layout={{ hovermode: "x unified", xaxis: { title: "" }, yaxis: { title: activeSummary?.units_short || "" } }} rowCount={observations.length} title={activeSummary?.indicator || activeId} />
          </div>
          <DataTable columns={[
            { key: "indicator", label: "Indicator" },
            { key: "latest_value", label: "Latest", align: "right", render: (value, row) => formatValue(value, row.units_short) },
            { key: "prev_delta", label: "Change", align: "right", render: (value, row) => formatDelta(value, row.units_short) },
            { key: "yoy_delta", label: "YoY", align: "right", render: (value, row) => formatDelta(value, row.units_short) },
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
