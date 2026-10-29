"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityRows,
  ApprovedCallDetail,
  CallRows,
  PaperCallDetail,
  PaperOrderReview,
  PortfolioTimeline,
  ProposalCard,
  riskLabels,
  money,
  pct,
  type OrderAction,
  type PaperCall,
  type PaperPortfolio,
  type RiskPolicy,
  type TrackedPaperCall,
  type TradingPolicy,
  type Workspace,
} from "@/components/trading-paper-portfolio";
import { MetricStrip, WorkbookTabs } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import { useAuthenticatedSession } from "@/lib/client/session";
import styles from "@/components/trading-paper-portfolio.module.css";

type TradingView = {
  paper_portfolio: PaperPortfolio;
  workspace: Workspace;
  tracked_calls: TrackedPaperCall[];
};
export default function TradingAgentPage() {
  const { session, sessionError } = useAuthenticatedSession("/trading-agent");
  const [view, setView] = useState<TradingView | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [period, setPeriod] = useState("1M"),
    [section, setSection] = useState("activity"),
    [busy, setBusy] = useState(false),
    [refreshing, setRefreshing] = useState(false);
  const [review, setReview] = useState<OrderAction | null>(null),
    [detail, setDetail] = useState<PaperCall | null>(null),
    [settings, setSettings] = useState(false);
  const [research, setResearch] = useState<TrackedPaperCall | null>(null);
  const refreshingRef = useRef(false),
    loadId = useRef(0);
  const periodRef=useRef("1M");
  const load = useCallback(async () => {
    const id = ++loadId.current;
    const requestedPeriod=periodRef.current;
    try {
      const response = await fetch(`/api/trading-agent?period=${requestedPeriod}`, {
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(
          await responseError(response, "Trading could not be loaded."),
        );
      const next = await response.json();
      if (id === loadId.current && requestedPeriod===periodRef.current) {
        setView(next);
        setError("");
      }
    } catch (e) {
      if (id === loadId.current && requestedPeriod===periodRef.current)
        setError(e instanceof Error ? e.message : "Trading unavailable.");
    }
  }, []);
  const refresh = useCallback(async () => {
    if (refreshingRef.current) return;
    refreshingRef.current = true;
    setRefreshing(true);
    try {
      const r = await secureMutation("/api/trading-agent/paper/refresh", {
        method: "POST",
        body: "{}",
      });
      if (!r.ok)
        throw new Error(await responseError(r, "Account refresh failed."));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed.");
    } finally {
      setRefreshing(false);
      refreshingRef.current = false;
    }
  }, [load]);
  useEffect(() => {
    if (!session?.authenticated) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [session?.authenticated, load,period]);
  const linked =
    view?.paper_portfolio &&
    !["unlinked", "unavailable"].includes(view.paper_portfolio.status);
  useEffect(() => {
    if (!session?.authenticated || !linked) return;
    const tick = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = window.setTimeout(tick, 0),
      interval = window.setInterval(tick, 60_000);
    window.addEventListener("focus", tick);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
      window.removeEventListener("focus", tick);
    };
  }, [linked, refresh, session?.authenticated]);
  async function deny(call: PaperCall) {
    setBusy(true);
    try {
      const r = await secureMutation("/api/trading-agent/decisions", {
        method: "POST",
        body: JSON.stringify({
          candidate_id: call.candidate_id,
          action: "reject",
        }),
      });
      if (!r.ok) throw new Error(await responseError(r, "Decision failed."));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision failed.");
    } finally {
      setBusy(false);
    }
  }
  async function updatePolicy(next: TradingPolicy) {
    setBusy(true);
    try {
      const r = await secureMutation("/api/trading-agent/paper/policy", {
        method: "POST",
        body: JSON.stringify({
          mode: next.mode,
          entries_paused: next.entries_paused,
          risk_policy: next.risk_policy,
          expected_revision: next.revision,
        }),
      });
      if (!r.ok)
        throw new Error(
          await responseError(r, "Trading settings could not be saved."),
        );
      setSettings(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings unavailable.");
    } finally {
      setBusy(false);
    }
  }
  function openCall(id: string) {
    const c =
      view?.workspace.calls.find((c) => c.call_id === id) ||
      view?.workspace.proposals.find((c) => c.call_id === id);
    if (c) setDetail(c);
  }
  async function earlier() {
    if (!view?.workspace.next_cursor) return;
    setBusy(true);
    try {
      const r = await fetch(
        `/api/trading-agent?period=${period}&cursor=${encodeURIComponent(view.workspace.next_cursor)}`,
        { cache: "no-store" },
      );
      if (!r.ok)
        throw new Error(await responseError(r, "Activity unavailable."));
      const next = (await r.json()) as TradingView;
      setView((current) =>
        current
          ? {
              ...current,
              workspace: {
                ...current.workspace,
                activity: [
                  ...current.workspace.activity,
                  ...next.workspace.activity.filter(
                    (e) =>
                      !current.workspace.activity.some(
                        (old) => old.id === e.id,
                      ),
                  ),
                ],
                next_cursor: next.workspace.next_cursor,
              },
            }
          : current,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Activity unavailable.");
    } finally {
      setBusy(false);
    }
  }
  if (!session?.authenticated)
    return (
      <div className="page-shell">
        <p>Checking session…</p>
      </div>
    );
  const workspace = view?.workspace,
    policy = workspace?.policy;
  const pending =
    workspace?.proposals.filter((c) => c.status === "pending") || [];
  const executed =
    workspace?.calls.filter((c) => c.status && c.status !== "not_filled") || [];
  return (
    <main className={`page-shell ${styles.workspace}`}>
      <header className={styles.line}>
        <h1>Trading</h1>
        {policy ? (
          <div className={styles.actions}>
            <span className={styles.mode}>
              {policy.entries_paused
                ? "Entries paused"
                : policy.mode === "autonomous"
                  ? "Paper autopilot"
                  : "Approve each order"}
            </span>
            <button
              className="quiet-button"
              onClick={() => setSettings(true)}
              aria-label="Trading settings"
            >
              Settings
            </button>
          </div>
        ) : null}
      </header>
      {sessionError || error ? (
        <p className="subtle-error" role="alert">
          {sessionError || error}
        </p>
      ) : null}
      {notice ? <p role="status">{notice}</p> : null}
      <PortfolioTimeline
        portfolio={view?.paper_portfolio}
        period={period}
        onPeriod={value=>{periodRef.current=value;setPeriod(value);}}
        refreshing={refreshing}
        onRefresh={() => void refresh()}
      />
      {policy ? (
        <div className={styles.line}>
          <span className={styles.meta}>
            {pending.length} awaiting approval ·{" "}
            {executed.filter((c) => c.status === "open").length} open calls
          </span>
          <button
            className="quiet-button"
            disabled={busy}
            onClick={() =>
              void updatePolicy({
                ...policy,
                entries_paused: !policy.entries_paused,
              })
            }
          >
            {policy.entries_paused ? "Resume entries" : "Pause entries"}
          </button>
        </div>
      ) : null}
      <WorkbookTabs
        active={section}
        onChange={setSection}
        label="Trading"
        items={[
          { key: "activity", label: "Activity" },
          { key: "positions", label: "Positions" },
          { key: "performance", label: "Performance" },
        ]}
      />
      {section === "activity" ? (
        <>
          {pending.map((c) => (
            <ProposalCard
              key={c.candidate_id}
              call={c}
              busy={busy}
              onApprove={() =>
                setReview({
                  ticker: c.ticker,
                  candidate_id: c.candidate_id,
                  intent: c.intent,
                })
              }
              onDeny={() => void deny(c)}
              onDetail={() => setDetail(c)}
            />
          ))}
          {workspace?.working_orders.length ? (
            <section className="workbook-panel">
              <header className="panel-header">
                <h2>Working orders</h2>
              </header>
              {workspace.working_orders.map((o) => (
                <div className={styles.order} key={String(o.client_order_id)}>
                  <div>
                    <strong>{String(o.symbol)}</strong>
                    <p>
                      {String(o.side)} {String(o.qty)} ·{" "}
                      {String(o.filled_qty || "0")} filled ·{" "}
                      {String(o.status).replaceAll("_", " ")}
                    </p>
                  </div>
                  <button
                    className="quiet-button"
                    disabled={!o.id}
                    onClick={() =>
                      setReview({
                        ticker: String(o.symbol),
                        intent: "cancel",
                        order_id: String(o.id),
                      })
                    }
                  >
                    Review cancellation
                  </button>
                </div>
              ))}
            </section>
          ) : null}
          {workspace?.activity.length ? (
            <ActivityRows events={workspace.activity} onSelect={openCall} />
          ) : !pending.length ? (
            <section className={styles.empty}>
              <h2>No trading activity yet</h2>
              <p>New calls and position reviews appear here.</p>
            </section>
          ) : null}
          {workspace?.next_cursor ? (
            <button
              className="quiet-button"
              disabled={busy}
              onClick={() => void earlier()}
            >
              Earlier activity
            </button>
          ) : null}
          {workspace?.proposals.some((c) => c.status === "expired") ? (
            <details className="workbook-panel">
              <summary>Expired calls</summary>
              <CallRows
                calls={workspace.proposals.filter(
                  (c) => c.status === "expired",
                )}
                onSelect={setDetail}
              />
            </details>
          ) : null}
        </>
      ) : null}
      {section === "positions" ? (
        <>
          <section className="workbook-panel">
            <header className="panel-header">
              <h2>Open calls</h2>
            </header>
            {executed.some((c) => c.status === "open") ? (
              <CallRows
                calls={executed.filter((c) => c.status === "open")}
                onSelect={setDetail}
              />
            ) : (
              <p className={styles.body}>No open calls.</p>
            )}
          </section>
          <section className="workbook-panel">
            <header className="panel-header">
              <h2>Account positions</h2>
            </header>
            {view?.paper_portfolio.holdings?.length ? (
              view.paper_portfolio.holdings.map((p) => (
                <div className={styles.order} key={String(p.symbol)}>
                  <div>
                    <strong>
                      {String(p.symbol)} · {String(p.qty)} shares
                    </strong>
                    <p>
                      {money(p.market_value)} · Unrealized{" "}
                      {money(p.unrealized_pl)}
                    </p>
                  </div>
                  {executed.some(
                    (c) => c.ticker === String(p.symbol) && c.status === "open",
                  ) ? (
                    <details>
                      <summary>Manage calls</summary>
                      <CallRows
                        calls={executed.filter(
                          (c) =>
                            c.ticker === String(p.symbol) &&
                            c.status === "open",
                        )}
                        onSelect={setDetail}
                      />
                    </details>
                  ) : (
                    <button
                      className="quiet-button"
                      onClick={() =>
                        setReview({
                          ticker: String(p.symbol),
                          symbol: String(p.symbol),
                          intent: "close",
                        })
                      }
                    >
                      Review close
                    </button>
                  )}
                </div>
              ))
            ) : (
              <p className={styles.body}>No open positions.</p>
            )}
          </section>
        </>
      ) : null}
      {section === "performance" ? (
        <>
          <section className="workbook-panel">
            <header className="panel-header">
              <h2>Agent track record · all time</h2>
            </header>
            <MetricStrip
              items={[
                {
                  label: "Filled calls",
                  value: workspace?.track_record.executed_calls ?? 0,
                },
                {
                  label: "Closed calls",
                  value: workspace?.track_record.closed_calls ?? 0,
                },
                {
                  label: "Realized P/L",
                  value: money(workspace?.track_record.realized_pl),
                },
                {
                  label: "Unrealized P/L",
                  value: money(workspace?.track_record.unrealized_pl),
                },
                {
                  label: "Win rate · closed calls",
                  value: pct(workspace?.track_record.win_rate_pct),
                },
                {
                  label: "Profit factor · closed calls",
                  value:
                    workspace?.track_record.profit_factor?.toFixed(2) ?? "—",
                },
              ]}
            />
            {workspace?.track_record.calls_needing_reconciliation ? (
              <p className={styles.body}>
                {workspace.track_record.calls_needing_reconciliation} calls need
                reconciliation.
              </p>
            ) : null}
            {executed.length ? (
              <CallRows calls={executed} onSelect={setDetail} />
            ) : (
              <p className={styles.body}>No filled calls yet.</p>
            )}
          </section>
          {view?.tracked_calls?.length ? (
            <details className="workbook-panel">
              <summary>
                Research observations · {view.tracked_calls.length}
              </summary>
              <p className={styles.meta}>
                Price performance from research approvals.
              </p>
              <div className={styles.calls}>
                {view.tracked_calls.map((c) => (
                  <button key={c.tracking_id} onClick={() => setResearch(c)}>
                    <span>
                      {c.ticker} · {c.horizon_label}
                    </span>
                    <span>View research →</span>
                  </button>
                ))}
              </div>
            </details>
          ) : null}
          {research ? (
            <section>
              <button
                className="quiet-button"
                onClick={() => setResearch(null)}
              >
                Close research
              </button>
              <ApprovedCallDetail call={research} />
            </section>
          ) : null}
        </>
      ) : null}
      {detail ? (
        <PaperCallDetail
          key={detail.call_id}
          call={detail}
          onClose={() => setDetail(null)}
          onAction={(action) => {
            setDetail(null);
            setReview(action);
          }}
        />
      ) : null}
      {review ? (
        <PaperOrderReview
          key={JSON.stringify(review)}
          candidate={review}
          initialPolicy={policy?.risk_policy}
          onClose={() => setReview(null)}
          onSubmitted={async (o) => {
            setReview(null);
            setNotice(
              review.intent === "cancel"
                ? "Cancellation requested."
                : `Paper order submitted: ${o.side} ${o.qty} ${o.symbol}`,
            );
            await refresh();
          }}
        />
      ) : null}
      {settings && policy ? (
        <PolicySettings
          policy={policy}
          busy={busy}
          onClose={() => setSettings(false)}
          onSave={(next) => void updatePolicy(next)}
        />
      ) : null}
    </main>
  );
}
function PolicySettings({
  policy,
  busy,
  onSave,
  onClose,
}: {
  policy: TradingPolicy;
  busy: boolean;
  onSave: (p: TradingPolicy) => void;
  onClose: () => void;
}) {
  const [next, setNext] = useState(policy);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className={styles.overlay}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <section className={`workbook-panel ${styles.review}`}>
        <header className="panel-header">
          <h2>Trading settings</h2>
          <button className="quiet-button" disabled={busy} onClick={onClose}>
            Close
          </button>
        </header>
        <div className={styles.body}>
          <label className={styles.setting}>
            Execution
            <select
              value={next.mode}
              onChange={(e) =>
                setNext({
                  ...next,
                  mode: e.target.value as TradingPolicy["mode"],
                })
              }
            >
              <option value="approval">Approve each order</option>
              <option value="autonomous">Paper autopilot</option>
            </select>
          </label>
          {next.mode === "autonomous" ? (
            <p>
              The agent can place new paper entries and position changes within
              these limits on each Trading run. Existing proposals still need
              your approval.
            </p>
          ) : null}
          <label className={styles.setting}>
            <input
              type="checkbox"
              checked={next.entries_paused}
              onChange={(e) =>
                setNext({ ...next, entries_paused: e.target.checked })
              }
            />
            Pause new entries
          </label>
          <div className={styles.riskInputs}>
            {Object.entries(riskLabels).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  min="0.1"
                  max="100"
                  step="0.1"
                  value={next.risk_policy[key as keyof RiskPolicy]}
                  onChange={(e) =>
                    setNext({
                      ...next,
                      risk_policy: {
                        ...next.risk_policy,
                        [key]: Number(e.target.value),
                      },
                    })
                  }
                />
              </label>
            ))}
          </div>
        </div>
        <button
          className="primary-button"
          disabled={busy}
          onClick={() => onSave(next)}
        >
          {busy
            ? "Saving…"
            : next.mode === "autonomous" && policy.mode !== "autonomous"
              ? "Enable paper autopilot"
              : "Save settings"}
        </button>
      </section>
    </dialog>
  );
}
