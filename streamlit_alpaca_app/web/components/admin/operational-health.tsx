"use client";

import { useEffect, useState } from "react";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip } from "@/components/workbook";
import { responseError } from "@/lib/client/api";
import styles from "./operational-health.module.css";

type Row = Record<string, unknown>;
type Point = { time: string; average: number | null; maximum: number | null };
type Series = { metric: string; unit: string; points: Point[] };
type Health = {
  hours: number; scope: string; resources: { name: string; kind: string; scope: string }[];
  outputs?: Row[];
  maintenance?: { reports?: Row[]; attempts?: Row[]; unavailable?: Record<string, string> };
  cloud_unavailable?: string;
  metrics?: { observed_at?: string; resource: string; series: Series[]; unavailable?: string; allocation_unavailable?: string;
    allocation?: { containers: { name: string; cpu?: number; memory?: string }[] } };
  database: { observed_at: string; server_name?: string; receipt_source?: string; attempts?: Row[]; usage_trend?: Row[]; connections?: Row[];
    capacity?: Row[]; components?: Row[]; learning?: Row[]; unavailable?: Record<string, string> };
};
const numeric = (value: unknown) => value === null || value === undefined ? null : Number(value);
const format = (value: unknown) => numeric(value)?.toLocaleString(undefined, { maximumFractionDigits: 2 }) ?? "—";
const date = (value: unknown) => value ? new Date(String(value)).toLocaleString() : "—";
const names: Record<string, string> = {
  cpu_percent: "Database CPU (%)", memory_percent: "Database memory (%)", active_connections: "Server connections",
  connections_failed: "Rejected connections", storage_percent: "Storage (%)", UsageNanoCores: "CPU · resource total (cores)",
  UsageBytes: "Memory · resource total (MiB)", WorkingSetBytes: "Memory · resource total (MiB)",
  Replicas: "Replicas", RestartCount: "Restarts",
};

export function OperationalHealth() {
  const [hours, setHours] = useState(24);
  const [resource, setResource] = useState("");
  const [view, setView] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    const query = new URLSearchParams({ hours: String(hours), resource });
    void fetch(`/api/admin/health?${query}`, { credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response, "Health data is unavailable."));
        const data = await response.json() as Health;
        if (controller.signal.aborted) return;
        setView(data);
        if (!resource && data.resources.length) setResource(data.resources.find((item) => item.name === data.database.server_name)?.name || data.resources.find((item) => item.kind.includes("PostgreSQL"))?.name || data.resources[0].name);
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Health data is unavailable."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [hours, resource, refresh]);
  const db = view?.database;
  const attempts = db?.attempts || [];
  const total = (key: string) => {
    const values = attempts.map((row) => numeric(row[key])).filter((value): value is number => value !== null);
    return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
  };
  const trend = db?.usage_trend || [];
  const cloud = view?.metrics;
  return <>
    <section className={`workbook-panel ${styles.summary}`}>
      <header className="panel-header"><h2>Health</h2><div className={styles.controls}>
        <label className="compact-control">Window<select value={hours} onChange={(event) => setHours(Number(event.target.value))}>
          {[1, 24, 72, 168].map((value) => <option key={value} value={value}>{value === 1 ? "1 hour" : value === 24 ? "24 hours" : `${value / 24} days`}</option>)}
        </select></label>
        <button className="quiet-button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Refresh</button>
      </div></header>
      {error ? <p role="alert" className="subtle-error">{error}</p> : null}
      {db ? <p className={styles.metadata}>{view?.scope} · observed {date(db.observed_at)}{db.receipt_source ? ` · receipts: ${db.receipt_source}` : ""}</p> : loading ? <p>Loading health…</p> : null}
      {db?.unavailable ? <p role="status" className="subtle-error">Unavailable: {Object.keys(db.unavailable).join(", ")}</p> : null}
      <MetricStrip items={[
        { label: "Recorded HTTP attempts", value: format(total("attempts")) }, { label: "Failed attempts", value: format(total("failures")) },
        { label: "Input tokens", value: format(total("input_tokens")) }, { label: "Output tokens", value: format(total("output_tokens")) },
        { label: "Cached input tokens", value: format(total("cached_tokens")) }, { label: "Usage unknown", value: format(total("unknown_usage")) },
        { label: "Known cost estimate (USD, upper)", value: format(total("estimated_usd_max")) }, { label: "Unpriced attempts", value: format(total("unpriced_attempts")) },
      ]} />
    </section>
    {view?.maintenance ? <section className="workbook-panel"><header className="panel-header"><h2>Isolated dev maintenance</h2></header>
      {view.maintenance.unavailable ? <p role="status" className="subtle-error">Unavailable: {Object.values(view.maintenance.unavailable).join(", ")}</p> : null}
      <DataTable rows={view.maintenance.reports || []} rowKey={(row) => String(row.run_id)} columns={[
        { key: "created_at_utc", label: "Started", render: date }, { key: "research_status", label: "Research" },
        { key: "edits", label: "Edits", align: "right", render: format },
        { key: "run_id", label: "Run" },
      ]} />
      <DataTable<Row> rows={(view.maintenance.reports || []).flatMap((report) =>
        (Array.isArray(report.articles) ? report.articles as Row[] : []).map((article) => ({ ...article, run_id: report.run_id })))}
        rowKey={(row, index) => `${row.run_id}-${index}`} columns={[
          { key: "page_id", label: "Article" }, { key: "status", label: "Result" }, { key: "error", label: "Failure" },
        ]} />
      <DataTable rows={view.maintenance.attempts || []} rowKey={(_, index) => String(index)} columns={[
        { key: "model", label: "Model" }, { key: "purpose", label: "Purpose" },
        ...["attempts", "failures", "input_tokens", "output_tokens", "unknown_usage", "unpriced_attempts"].map((key) =>
          ({ key, label: key.replaceAll("_", " "), align: "right" as const, render: format })),
        { key: "estimated_usd_max", label: "Known cost (USD, upper)", align: "right", render: format },
      ]} />
    </section> : null}
    <section className="workbook-panel"><header className="panel-header"><h2>Published outputs</h2></header>
      <DataTable rows={view?.outputs || []} rowKey={(row) => String(row.dataset)} columns={[
        { key: "dataset", label: "Dataset" }, { key: "asof", label: "As of", render: date },
        { key: "published_at", label: "Published", render: date }, { key: "rows", label: "Rows", align: "right", render: format },
        { key: "version", label: "Version" }, { key: "status", label: "Unavailable" },
      ]} />
    </section>
    {trend.length ? <section className="workbook-panel"><div className={`workbook-chart ${styles.chart}`}><DataPlot id={`health-tokens-${hours}`} title="LLM tokens" rowCount={trend.length}
      data={["input_tokens", "output_tokens"].map((key) => ({ type: "scatter", mode: "lines+markers", name: key === "input_tokens" ? "Input" : "Output", x: trend.map((row) => row.time), y: trend.map((row) => numeric(row[key])) }))}
      layout={{ xaxis: { type: "date" }, yaxis: { title: "Tokens per hour" } }} /></div></section> : null}
    <section className="workbook-panel"><header className="panel-header"><h2>Resource usage</h2>
      <label className="compact-control">Resource<select value={resource} onChange={(event) => setResource(event.target.value)}>
        <option value="">Select resource</option>{view?.resources.map((item) => <option key={item.name} value={item.name}>{item.name}{item.scope === "shared" ? " · shared" : ""}</option>)}
      </select></label></header>
      {view?.cloud_unavailable || cloud?.unavailable ? <p className="subtle-error" role="status">{view?.cloud_unavailable || cloud?.unavailable}</p> : null}
      {cloud?.observed_at ? <p className={styles.metadata}>{cloud.resource} · observed {date(cloud.observed_at)}</p> : null}
      {cloud?.allocation_unavailable ? <p className="subtle-error" role="status">Allocation unavailable: {cloud.allocation_unavailable}</p> : null}
      {cloud?.allocation?.containers.map((item) => <p className={styles.metadata} key={item.name}>{item.name} · {item.cpu} CPU · {item.memory} per replica</p>)}
      <div className="admin-form-grid">{cloud?.series.map((series, index) => {
        const scale = series.metric.includes("Bytes") ? 1024 ** 2 : series.metric === "UsageNanoCores" ? 1e9 : 1;
        return <div key={`${series.metric}-${index}`} className={`workbook-chart ${styles.chart}`}><DataPlot id={`health-${resource}-${series.metric}-${index}-${hours}`} title={names[series.metric] || series.metric} rowCount={series.points.length}
          data={["average", "maximum"].map((key) => ({ type: "scatter", mode: "lines", name: key === "average" ? "Average" : "Peak", x: series.points.map((point) => point.time), y: series.points.map((point) => { const value = point[key as "average" | "maximum"]; return value === null ? null : value / scale; }) }))}
          layout={{ xaxis: { type: "date" }, yaxis: { rangemode: "tozero" } }} /></div>;
      })}</div>
    </section>
    <section className="workbook-panel"><header className="panel-header"><h2>Provider attempts</h2></header>
      <DataTable rows={attempts} rowKey={(row, index) => `${row.provider}-${row.purpose}-${index}`} columns={[
        { key: "model", label: "Model" }, { key: "surface", label: "Surface" }, { key: "purpose", label: "Purpose" },
        ...["operations", "attempts", "failures", "unfinished", "unknown_usage", "input_tokens", "peak_input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens", "unpriced_attempts"].map((key) => ({ key, label: key.replaceAll("_", " "), align: "right" as const, render: format })),
        { key: "estimated_usd_max", label: "Known cost estimate (USD, upper)", align: "right", render: format },
        { key: "p95_ms", label: "P95 ms", align: "right", render: format },
      ]} />
    </section>
    <section className="workbook-panel"><header className="panel-header"><h2>Pipeline database connections · shared server</h2></header>
      <MetricStrip items={(db?.capacity || []).map((row) => ({ label: String(row.name).replaceAll("_", " "), value: format(row.value) }))} />
      <DataTable rows={db?.connections || []} rowKey={(_, index) => String(index)} columns={[
        { key: "database", label: "Database" }, { key: "application", label: "Application" }, { key: "state", label: "State" },
        { key: "wait_event_type", label: "Wait" }, { key: "connections", label: "Connections", align: "right", render: format },
        { key: "oldest_transaction_seconds", label: "Oldest transaction (s)", align: "right", render: format },
      ]} />
    </section>
    <section className="workbook-panel"><header className="panel-header"><h2>Learning episodes · shared store</h2></header>
      <DataTable rows={db?.learning || []} rowKey={(row) => String(row.status)} columns={[
        { key: "status", label: "State" }, { key: "episodes", label: "Episodes", align: "right", render: format },
        { key: "updated_at", label: "Updated", render: date },
      ]} />
    </section>
    <section className="workbook-panel"><header className="panel-header"><h2>Latest executions</h2></header>
      <DataTable rows={db?.components || []} rowKey={(row) => String(row.job_name)} columns={[
        { key: "job_name", label: "Job" }, { key: "status", label: "State" }, { key: "progress_stage", label: "Stage" },
        { key: "progress_message", label: "Result" }, { key: "start_time_utc", label: "Started", render: date },
        { key: "end_time_utc", label: "Ended", render: date }, { key: "platform_execution_name", label: "Execution" },
      ]} />
    </section>
  </>;
}
