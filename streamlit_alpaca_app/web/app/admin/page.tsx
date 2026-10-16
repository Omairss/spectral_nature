"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { LLMSettingsEditor, type LLMSettings } from "@/components/admin/llm-settings";
import { OperationalHealth } from "@/components/admin/operational-health";
import { DataPlot } from "@/components/visualizations/data-plot";
import { DataTable, MetricStrip, WorkbookTabs } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";

type Rows = Array<Record<string, unknown>>;
type AdminView = {
  users: Rows; invites: Rows; access: Record<string, unknown> & { summary?: Record<string, unknown>; user_usage?: Rows; section_usage?: Rows; active_sessions?: Rows; recent_security_events?: Rows; access_ips?: Rows };
  llm: LLMSettings;
  agent_keys: Rows;
  system: { job_status: Rows; dataset_status: Rows; job_history: Rows; dataset_history: Rows; connector_calls: Rows; model_calls: Rows };
  auth: { email_delivery: Record<string, unknown>; scopes: string[] };
  history_days: number;
};
function number(value: unknown) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
function date(value: unknown) { const parsed = new Date(String(value || "")); return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString(); }
function pct(value: unknown) { const parsed = number(value); return parsed === null ? "—" : `${(parsed * 100).toFixed(2)}%`; }

export default function AdminPage() {
  const { session, sessionError } = useAuthenticatedSession("/admin");
  const [view, setView] = useState<AdminView | null>(null);
  const [tab, setTab] = useState("users");
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("investor");
  const [inviteShare, setInviteShare] = useState("0");
  const [resetEmail, setResetEmail] = useState("");

  const load = useCallback(async () => {
    await Promise.resolve();
    setLoading(true); setError("");
    try {
      const response = await fetch(`/api/admin?days=${days}`, { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error(await responseError(response, response.status === 403 ? "Admin access is required." : "Admin data could not be loaded."));
      setView((await response.json()) as AdminView);
    } catch (reason) { setView(null); setError(reason instanceof Error ? reason.message : "Admin data could not be loaded."); }
    finally { setLoading(false); }
  }, [days]);
  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load, session]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoading(true); setError(""); setNotice("");
    try {
      const share = inviteRole === "investor" ? Number(inviteShare) / 100 : 0;
      const response = await secureMutation("/api/admin/invites", { method: "POST", body: JSON.stringify({ email: inviteEmail, role: inviteRole, share_fraction: share }) });
      if (!response.ok) throw new Error(await responseError(response, "Invite could not be created."));
      const result = await response.json() as Record<string, unknown>;
      setNotice(String(result.email_sent ? "Invite sent." : result.invite_url || result.message || "Invite created."));
      setInviteEmail(""); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Invite could not be created."); setLoading(false); }
  }
  async function reset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoading(true); setError(""); setNotice("");
    try {
      const response = await secureMutation("/api/admin/password-resets", { method: "POST", body: JSON.stringify({ email: resetEmail }) });
      if (!response.ok) throw new Error(await responseError(response, "Password reset could not be issued."));
      const result = await response.json() as Record<string, unknown>;
      setNotice(String(result.email_sent ? "Password reset sent." : result.reset_url || result.message || "Password reset issued."));
      setResetEmail("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Password reset could not be issued."); }
    finally { setLoading(false); }
  }

  const access = view?.access;
  const summary = access?.summary || {};
  const system = view?.system;
  const failedJobs = system?.job_status.filter((row) => String(row.status || "").toLowerCase() === "failed").length || 0;
  const runningJobs = system?.job_status.filter((row) => String(row.status || "").toLowerCase() === "running").length || 0;
  const sectionUsage = access?.section_usage || [];
  const userUsage = access?.user_usage || [];
  const connectorFailures = useMemo(() => (system?.connector_calls || []).reduce((sum, row) => sum + (number(row.failure_count) || 0), 0), [system]);

  if (!session?.authenticated) return <div className="page-shell"><p className="loading-label">Checking session…</p></div>;
  return (
    <div className="page-shell workbook-page wide-workbook">
      <header className="artifact-header workbook-heading"><div><h1>Admin</h1></div>{!["llm", "health"].includes(tab) ? <label className="compact-control">History<select onChange={(event) => setDays(Number(event.target.value))} value={days}>{[3, 7, 14, 30].map((value) => <option key={value} value={value}>{value} days</option>)}</select></label> : null}</header>
      <WorkbookTabs active={tab} items={[{ key: "users", label: "Users" }, { key: "access", label: "Usage & Security" }, { key: "llm", label: "LLM" }, { key: "keys", label: "API Keys" }, { key: "system", label: "System" }, { key: "health", label: "Health" }]} label="Admin sections" onChange={setTab} />
      {tab === "health" ? <OperationalHealth /> : null}
      {sessionError || error ? <p className="subtle-error" role="alert">{sessionError || error}</p> : null}
      {notice ? <p className="admin-notice" role="status">{notice}</p> : null}
      {loading && !view ? <p className="loading-label">Loading admin workspace…</p> : null}
      {view && tab === "users" ? <>
        <MetricStrip items={[
          { label: "Users", value: view.users.length }, { label: "Pending invites", value: view.invites.filter((row) => row.status === "pending").length },
          { label: "Open sessions", value: view.users.reduce((sum, row) => sum + (number(row.open_session_count) || 0), 0) },
          { label: "Active sessions", value: view.users.reduce((sum, row) => sum + (number(row.active_session_count) || 0), 0) },
        ]} />
        <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Accounts</p><h2>Users</h2></div><span>{view.users.length}</span></header>
          <DataTable columns={[{ key: "display_name", label: "Name", render: (value, row) => String(value || row.email || "—") }, { key: "email", label: "Email" }, { key: "role", label: "Role" }, { key: "membership_role", label: "Portfolio role" }, { key: "share_fraction", label: "Share", align: "right", render: pct }, { key: "status", label: "Status" }, { key: "active_session_count", label: "Active", align: "right" }, { key: "last_seen_at", label: "Last seen", render: date }]} rowKey={(row, index) => String(row.user_id || index)} rows={view.users} />
        </section>
        <section className="admin-form-grid">
          <form className="workbook-panel admin-form" onSubmit={invite}><header><p className="section-label">Access</p><h2>Invite user</h2></header><label>Email<input onChange={(event) => setInviteEmail(event.target.value)} required type="email" value={inviteEmail} /></label><label>Role<select onChange={(event) => setInviteRole(event.target.value)} value={inviteRole}><option value="investor">Investor</option><option value="viewer">Viewer</option><option value="admin">Admin</option></select></label>{inviteRole === "investor" ? <label>Economic share (%)<input max="100" min="0" onChange={(event) => setInviteShare(event.target.value)} step="0.01" type="number" value={inviteShare} /></label> : null}<button className="primary-button" disabled={loading} type="submit">Create invite</button></form>
          <form className="workbook-panel admin-form" onSubmit={reset}><header><p className="section-label">Credentials</p><h2>Issue password reset</h2></header><label>User email<input onChange={(event) => setResetEmail(event.target.value)} required type="email" value={resetEmail} /></label><button className="quiet-button" disabled={loading} type="submit">Issue reset</button></form>
        </section>
        {view.invites.length ? <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Invitations</p><h2>Pending invites</h2></div></header><DataTable columns={[{ key: "email", label: "Email" }, { key: "role", label: "Role" }, { key: "proposed_share_fraction", label: "Share", align: "right", render: pct }, { key: "portfolio_slug", label: "Portfolio" }, { key: "status", label: "Status" }, { key: "expires_at", label: "Expires", render: date }]} rowKey={(row, index) => String(row.id || index)} rows={view.invites} /></section> : null}
      </> : null}
      {view && tab === "access" ? <>
        <MetricStrip items={[
          { label: "Total users", value: String(summary.total_users ?? view.users.length) }, { label: "Active users", value: String(summary.active_user_count ?? 0) },
          { label: "Open sessions", value: String(summary.open_session_count ?? 0) }, { label: "Locked users", value: String(summary.locked_user_count ?? 0) },
          { label: "Failed logins", value: String(summary.failed_login_count ?? 0) },
        ]} />
        <section className="workbook-panel split-visuals">
          <div className="workbook-chart"><DataPlot data={[{ type: "bar", x: sectionUsage.map((row) => String(row.section_name || row.section || "Unknown")), y: sectionUsage.map((row) => number(row.event_count ?? row.view_count ?? row.count)), marker: { color: "#52d6c7" } }]} id={`admin-section-usage-${days}`} layout={{ xaxis: { tickangle: -25 }, yaxis: { title: "Events" } }} rowCount={sectionUsage.length} title="Workspace usage" /></div>
          <div className="workbook-chart"><DataPlot data={[{ type: "bar", orientation: "h", y: userUsage.slice(0, 20).map((row) => String(row.display_name || row.email || "User")), x: userUsage.slice(0, 20).map((row) => number(row.event_count ?? row.activity_count ?? row.count)), marker: { color: "#a991f7" } }]} id={`admin-user-usage-${days}`} layout={{ margin: { l: 110, r: 24, t: 48, b: 48 } }} rowCount={userUsage.length} title="User activity" /></div>
        </section>
        <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Security</p><h2>Recent events</h2></div><span>{access?.recent_security_events?.length || 0}</span></header><DataTable columns={[{ key: "created_at", label: "Time", render: date }, { key: "email", label: "User" }, { key: "event_type", label: "Event" }, { key: "event_category", label: "Category" }, { key: "section_name", label: "Workspace" }, { key: "ip_address", label: "IP" }]} rowKey={(row, index) => String(row.id || index)} rows={(access?.recent_security_events || []).slice(0, 100)} /></section>
      </> : null}
      {view && tab === "llm" ? <>
        <LLMSettingsEditor settings={view.llm} onSaved={(llm) => setView((current) => current ? { ...current, llm } : current)} />
      </> : null}
      {view && tab === "keys" ? <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Agent access</p><h2>API keys</h2></div><span>{view.agent_keys.length}</span></header><DataTable columns={[{ key: "name", label: "Name" }, { key: "key_prefix", label: "Prefix" }, { key: "scopes", label: "Scopes", render: (value) => Array.isArray(value) ? value.join(", ") : String(value || "") }, { key: "status", label: "Status" }, { key: "created_at", label: "Created", render: date }, { key: "last_used_at", label: "Last used", render: date }, { key: "expires_at", label: "Expires", render: date }]} rowKey={(row, index) => String(row.id || index)} rows={view.agent_keys} /></section> : null}
      {view && tab === "system" ? <>
        <MetricStrip items={[
          { label: "Failed jobs", value: failedJobs }, { label: "Running jobs", value: runningJobs }, { label: "Datasets", value: system?.dataset_status.length || 0 },
          { label: "Connector failures", value: connectorFailures }, { label: "Model call groups", value: system?.model_calls.length || 0 },
        ]} />
        <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Pipeline</p><h2>Job status</h2></div></header><DataTable columns={[{ key: "job_name", label: "Job" }, { key: "status", label: "Status" }, { key: "progress_stage", label: "Stage" }, { key: "progress_pct", label: "Progress", align: "right" }, { key: "start_time_utc", label: "Started", render: date }, { key: "end_time_utc", label: "Ended", render: date }, { key: "message", label: "Message" }]} rowKey={(row, index) => `${row.job_name}-${index}`} rows={system?.job_status || []} /></section>
        <section className="workbook-panel"><header className="panel-header"><div><p className="section-label">Data plane</p><h2>Dataset freshness</h2></div></header><DataTable columns={[{ key: "dataset_name", label: "Dataset" }, { key: "row_count", label: "Rows", align: "right" }, { key: "age_hours", label: "Age (hours)", align: "right", render: (value) => number(value)?.toFixed(1) || "—" }, { key: "asof_time_utc", label: "As of", render: date }, { key: "ingested_at_utc", label: "Ingested", render: date }]} rowKey={(row, index) => String(row.dataset_name || index)} rows={system?.dataset_status || []} /></section>
        <section className="workbook-panel split-tables"><div><header className="panel-header"><div><p className="section-label">Connectors</p><h2>External calls</h2></div></header><DataTable columns={[{ key: "connector", label: "Connector" }, { key: "operation", label: "Operation" }, { key: "call_count", label: "Calls", align: "right" }, { key: "failure_count", label: "Failures", align: "right" }, { key: "p95_duration_ms", label: "P95 ms", align: "right" }]} rowKey={(row, index) => `${row.connector}-${row.operation}-${index}`} rows={system?.connector_calls || []} /></div><div><header className="panel-header"><div><p className="section-label">Models</p><h2>LLM calls</h2></div></header><DataTable columns={[{ key: "provider", label: "Provider" }, { key: "model", label: "Model" }, { key: "purpose", label: "Purpose" }, { key: "call_count", label: "Calls", align: "right" }, { key: "failure_count", label: "Failures", align: "right" }]} rowKey={(row, index) => `${row.provider}-${row.model}-${index}`} rows={system?.model_calls || []} /></div></section>
      </> : null}
    </div>
  );
}
