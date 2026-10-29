"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { DataPlot } from "@/components/visualizations/data-plot";
import { MetricStrip, DataTable } from "@/components/workbook";
import { responseError, secureMutation } from "@/lib/client/api";
import styles from "./trading-paper-portfolio.module.css";

type Observation = {
  date: string;
  return_pct: number | null;
  benchmark_return_pct: number | null;
};
export type TrackedPaperCall = {
  tracking_id: string;
  ticker: string;
  stance: string;
  horizon_label: string;
  approved_at_utc: string;
  as_of?: string;
  status: string;
  return_pct?: number;
  benchmark_return_pct?: number;
  excess_return_pct?: number;
  max_drawdown_pct?: number;
  entry_date?: string;
  last_price_date?: string;
  target_date?: string;
  hypothesis?: string;
  path?: Observation[];
  path_status?: string;
};
export type RiskPolicy = {
  position_volatility_pct: number;
  portfolio_volatility_pct: number;
  max_position_pct: number;
  max_gross_pct: number;
};
export type PaperPortfolio = {
  status: string;
  as_of?: string;
  period?: string;
  return_pct?: number;
  profit_loss?: number;
  max_drawdown_pct?: number;
  performance?: Record<string, number | null>;
  summary?: Record<string, number | string | boolean | null>;
  path?: Array<{
    date: string;
    equity: number | null;
    return_pct: number | null;
  }>;
  holdings?: Array<Record<string, string | number>>;
  orders?: Array<Record<string, string | number>>;
  unavailable?: string[];
  risk_policy?: RiskPolicy;
};
export type PaperCall = {
  candidate_id: string;
  ticker: string;
  stance: string;
  call_id: string;
  parent_call_id?: string;
  intent?: string;
  status?: string;
  hypothesis?: string;
  setup?: string;
  reason?: string;
  proposed_at?: string;
  horizon_label?: string;
  horizon_key?: string;
  quantity?: string;
  quantity_fraction?: number;
  pl?: number;
  realized_pl?: number;
  unrealized_pl?: number;
  return_pct?: number;
  average_entry?: number;
  entry_capital?: number;
  opened_at?: string;
  closed_at?: string;
  as_of?: string;
  fills?: Array<Record<string, unknown>>;
};
export type TradingPolicy = {
  mode: "approval" | "autonomous";
  entries_paused: boolean;
  revision: number;
  risk_policy: RiskPolicy;
};
export type PaperEvent = {
  id: string;
  kind: string;
  call_id?: string;
  occurred_at: string;
  payload: Record<string, unknown>;
};
export type Workspace = {
  status: string;
  proposals: PaperCall[];
  calls: PaperCall[];
  activity: PaperEvent[];
  working_orders: Array<Record<string, string | number>>;
  next_cursor?: string;
  policy: TradingPolicy;
  track_record: Record<string, number>;
  capabilities: Record<string, boolean>;
};
export type OrderAction = {
  candidate_id?: string;
  ticker: string;
  intent?: string;
  call_id?: string;
  quantity?: string;
  symbol?: string;
  order_id?: string;
};
const teal = "#52d6c7",
  violet = "#b89cff";
export function pct(value: number | null | undefined) {
  return value == null ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}
function numeric(value: unknown) {
  return value == null || value === "" || !Number.isFinite(Number(value))
    ? null
    : Number(value);
}
export function money(value: unknown) {
  const n = numeric(value);
  return n == null
    ? "—"
    : n.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      });
}
function day(value: string) {
  return value
    ? new Date(value).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";
}
export function timestamp(value?: string) {
  return value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "";
}
export const riskLabels: Record<keyof RiskPolicy, string> = {
  position_volatility_pct: "Position volatility budget (%)",
  portfolio_volatility_pct: "Portfolio volatility limit (%)",
  max_position_pct: "Maximum position (% equity)",
  max_gross_pct: "Maximum gross exposure (%)",
};

export function PortfolioTimeline({
  portfolio,
  period,
  onPeriod,
  refreshing,
  onRefresh,
}: {
  portfolio?: PaperPortfolio;
  period: string;
  onPeriod: (value: string) => void;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const summary = portfolio?.summary || {};
  const path = portfolio?.path || [];
  const available = ["available", "partial"].includes(portfolio?.status || "");
  const values = path
    .map((p) => p.equity)
    .filter((p): p is number => p != null);
  const low = Math.min(...values),
    high = Math.max(...values),
    pad =
      high === low ? Math.max(1, Math.abs(low) * 0.005) : (high - low) * 0.15;
  return (
    <section className={styles.hero}>
      <header className={styles.line}>
        <span className={styles.eyebrow}>Alpaca · Paper portfolio</span>
        <button
          className="quiet-button"
          disabled={refreshing || portfolio?.status === "unlinked"}
          onClick={onRefresh}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>
      {available ? (
        <>
          <div className={styles.equity}>{money(summary.equity)}</div>
          <div className={styles.change}>
            <strong>
              {money(portfolio?.profit_loss)}{" "}
              <span>({pct(portfolio?.return_pct)})</span>
            </strong>
            <span>
              {period === "ALL" ? "All time" : period} · cashflow adjusted
            </span>
          </div>
          {path.length ? (
            <div className={styles.chart}>
              <DataPlot
                id="paper-portfolio-timeline"
                title=""
                displayModeBar={false}
                data={[
                  {
                    type: "scatter",
                    mode: path.length === 1 ? "markers" : "lines",
                    name: "Account equity",
                    x: path.map((p) => p.date),
                    y: path.map((p) => p.equity),
                    connectgaps: false,
                    line: { color: teal, width: 2.5 },
                    fill: "tozeroy",
                    fillcolor: "rgba(82,214,199,.035)",
                  },
                ]}
                layout={{
                  height: 260,
                  margin: { l: 10, r: 80, t: 15, b: 35 },
                  xaxis: { type: "date", nticks: 4 },
                  yaxis: {
                    side: "right",
                    range: values.length ? [low - pad, high + pad] : undefined,
                    tickprefix: "$",
                    tickformat: ",.0f",
                    nticks: 3,
                    zeroline: false,
                  },
                  showlegend: false,
                  hovermode: "x unified",
                }}
                rowCount={path.length}
              />
            </div>
          ) : (
            <p>Equity history unavailable.</p>
          )}
          <nav className={styles.periods} aria-label="Portfolio period">
            {["1D", "1W", "1M", "3M", "1Y", "ALL"].map((p) => (
              <button
                key={p}
                aria-pressed={period === p}
                onClick={() => onPeriod(p)}
              >
                {p === "ALL" ? "All" : p}
              </button>
            ))}
          </nav>
          <MetricStrip
            items={[
              { label: "Cash", value: money(summary.cash) },
              { label: "Buying power", value: money(summary.buying_power) },
              {
                label: `${period} drawdown`,
                value: pct(portfolio?.max_drawdown_pct),
              },
              {
                label: `${period} Sharpe`,
                value: portfolio?.performance?.sharpe_ratio?.toFixed(2) ?? "—",
              },
            ]}
          />
          <div className={styles.meta}>
            Updated {timestamp(portfolio?.as_of)}
            {portfolio?.status === "partial"
              ? ` · Refresh incomplete: ${portfolio.unavailable?.join(", ")}`
              : ""}
          </div>
        </>
      ) : (
        <p className={styles.accountState}>
          {portfolio?.status === "unlinked"
            ? "No paper account connected."
            : portfolio?.status === "pending"
              ? "Refresh to load the paper account."
              : "Paper account unavailable."}
        </p>
      )}
    </section>
  );
}

export function ProposalCard({
  call,
  busy,
  onApprove,
  onDeny,
  onDetail,
}: {
  call: PaperCall;
  busy: boolean;
  onApprove: () => void;
  onDeny: () => void;
  onDetail: () => void;
}) {
  const action = call.intent || "entry";
  const expired = call.status === "expired";
  return (
    <article className={styles.proposal}>
      <div className={styles.line}>
        <span className={styles.eyebrow}>
          {expired
            ? "Expired call"
            : action === "entry"
              ? "New call · needs approval"
              : `${action} position · needs approval`}
        </span>
        <time>{timestamp(call.proposed_at)}</time>
      </div>
      <div className={styles.line}>
        <h3>
          {call.ticker} <span className={styles.stance}>{call.stance}</span>
        </h3>
        <span>{call.horizon_label || call.horizon_key?.toUpperCase()}</span>
      </div>
      <p>{call.reason || call.hypothesis || call.setup}</p>
      {call.quantity ? (
        <p>
          {call.quantity} shares to {action}
        </p>
      ) : null}
      <div className={styles.actions}>
        <button className="quiet-button" onClick={onDetail}>
          View thesis
        </button>
        <span className={styles.spacer} />
        <button className="quiet-button" disabled={busy} onClick={onDeny}>
          Deny
        </button>
        <button
          className="primary-button"
          disabled={busy || expired}
          onClick={onApprove}
        >
          Approve{action !== "entry" ? ` ${action}` : ""}
        </button>
      </div>
    </article>
  );
}

export function CallRows({
  calls,
  onSelect,
}: {
  calls: PaperCall[];
  onSelect: (call: PaperCall) => void;
}) {
  return (
    <div className={styles.calls}>
      {calls.map((c) => (
        <button key={c.call_id} onClick={() => onSelect(c)}>
          <span>
            <strong>{c.ticker}</strong> · {c.stance}
            <small>
              {c.status?.replaceAll("_", " ")} ·{" "}
              {c.horizon_label || c.horizon_key?.toUpperCase()} ·{" "}
              {c.quantity ?? "0"} shares
            </small>
          </span>
          <span>
            {money(c.pl)}
            <small>{pct(c.return_pct)} ›</small>
          </span>
        </button>
      ))}
    </div>
  );
}
const eventLabels: Record<string, string> = {
  position_review: "Position review",
  call_denied: "Call denied",
  submitted: "Order submitted",
  cancel_requested: "Cancellation requested",
  policy_changed: "Trading settings changed",
  action_needs_review: "Action needs review",
  monitor_unavailable: "Portfolio review unavailable",
};
export function ActivityRows({
  events,
  onSelect,
}: {
  events: PaperEvent[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className={styles.events}>
      {events.map((e) => (
        <article key={e.id} className={styles.event}>
          <div className={styles.eventDot} />
          <div>
            <div className={styles.line}>
              <strong>
                {String(e.payload.ticker || e.payload.symbol || "")}{" "}
                {eventLabels[e.kind] || e.kind.replaceAll("_", " ")}
              </strong>
              <time>{timestamp(e.occurred_at)}</time>
            </div>
            {e.payload.reason ? <p>{String(e.payload.reason)}</p> : null}
            {e.payload.action ? (
              <p>{String(e.payload.action).toUpperCase()}</p>
            ) : null}
            {e.payload.qty ? (
              <p>
                {String(e.payload.side)} {String(e.payload.qty)} ·{" "}
                {String(e.payload.filled_qty || "0")} filled
                {e.payload.filled_avg_price
                  ? ` at ${money(e.payload.filled_avg_price)}`
                  : ""}
              </p>
            ) : null}
            {e.call_id ? (
              <button
                className="quiet-button"
                onClick={() => onSelect(e.call_id!)}
              >
                View call →
              </button>
            ) : null}
          </div>
        </article>
      ))}
    </div>
  );
}

export function PaperCallDetail({
  call,
  onClose,
  onAction,
}: {
  call: PaperCall;
  onClose: () => void;
  onAction: (action: OrderAction) => void;
}) {
  const [detail, setDetail] = useState<{
    candidate: Record<string, unknown>;
    performance: PaperCall;
    path: Array<{ date: string; pl: number }>;
    activity: PaperEvent[];
  } | null>(null);
  const [error, setError] = useState("");
  const [quantity, setQuantity] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    fetch(
      `/api/trading-agent/paper/calls/${encodeURIComponent(call.call_id)}`,
      { cache: "no-store", signal: controller.signal },
    )
      .then(async (r) => {
        if (!r.ok)
          throw new Error(await responseError(r, "Call details unavailable."));
        return r.json();
      })
      .then(setDetail)
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e.message));
      });
    return () => controller.abort();
  }, [call.call_id]);
  const candidate: Record<string, unknown> = detail?.candidate || { ...call };
  const performance = detail?.performance || call;
  return (
    <dialog
      ref={dialog}
      className={styles.overlay}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <section className={`workbook-panel ${styles.review}`}>
        <header className="panel-header">
          <h2>
            {call.ticker} · {call.stance}
          </h2>
          <button className="quiet-button" onClick={onClose}>
            Done
          </button>
        </header>
        {error ? <p role="alert">{error}</p> : null}
        <MetricStrip
          items={[
            { label: "Call P/L", value: money(performance.pl) },
            {
              label: "Return on filled capital",
              value: pct(performance.return_pct),
            },
            { label: "Realized", value: money(performance.realized_pl) },
            { label: "Unrealized", value: money(performance.unrealized_pl) },
          ]}
        />
        {detail?.path.length ? (
          <DataPlot
            id={`paper-call-${call.call_id}`}
            title="Call P/L · USD"
            data={[
              {
                type: "scatter",
                mode: detail.path.length === 1 ? "markers" : "lines",
                x: detail.path.map((p) => p.date),
                y: detail.path.map((p) => p.pl),
                line: { color: teal },
              },
            ]}
            layout={{
              height: 260,
              xaxis: { type: "date" },
              yaxis: { tickprefix: "$" },
              showlegend: false,
            }}
            rowCount={detail.path.length}
          />
        ) : null}
        <div className={styles.body}>
          <p>{String(candidate.hypothesis || candidate.setup || "")}</p>
          {["reason", "invalidation"].map((key) =>
            candidate[key] ? (
              <div key={key}>
                <h3>
                  {key === "reason" ? "Latest recommendation" : "Invalidation"}
                </h3>
                <p>{String(candidate[key])}</p>
              </div>
            ) : null,
          )}
          {candidate.forecast && typeof candidate.forecast === "object" ? (
            <>
              <h3>At the call price</h3>
              <p>
                {String(
                  (candidate.forecast as Record<string, unknown>)
                    .pricing_assessment || "",
                )}
              </p>
              <h3>Opposing case</h3>
              <p>
                {String(
                  (candidate.forecast as Record<string, unknown>)
                    .opposing_case || "",
                )}
              </p>
            </>
          ) : null}
          {["evidence", "tail_risks", "evidence_limits"].map((key) =>
            Array.isArray(candidate[key]) &&
            (candidate[key] as unknown[]).length ? (
              <details key={key}>
                <summary>
                  {key === "tail_risks"
                    ? "Risks"
                    : key === "evidence_limits"
                      ? "Research next"
                      : "Evidence"}
                </summary>
                <ul>
                  {(candidate[key] as unknown[]).map((v, i) => (
                    <li key={i}>{String(v)}</li>
                  ))}
                </ul>
              </details>
            ) : null,
          )}
          <Link
            href={`/stock-investigator?ticker=${encodeURIComponent(call.ticker)}`}
          >
            Company details →
          </Link>
          {performance.status === "needs_reconciliation" ? (
            <p>Fill allocation needs reconciliation. Refresh the account.</p>
          ) : null}
          {performance.status === "open" ? (
            <div className={styles.management}>
              <strong>{performance.quantity} shares remaining</strong>
              <label>
                Shares to reduce
                <input
                  type="number"
                  min="0.000001"
                  max={performance.quantity}
                  step="any"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                />
              </label>
              <div className={styles.actions}>
                <button
                  className="quiet-button"
                  disabled={
                    !quantity ||
                    Number(quantity) <= 0 ||
                    Number(quantity) >= Number(performance.quantity)
                  }
                  onClick={() =>
                    onAction({
                      ticker: call.ticker,
                      intent: "reduce",
                      call_id: call.call_id,
                      quantity,
                    })
                  }
                >
                  Review reduction
                </button>
                <button
                  className="primary-button"
                  onClick={() =>
                    onAction({
                      ticker: call.ticker,
                      intent: "close",
                      call_id: call.call_id,
                    })
                  }
                >
                  Review close
                </button>
              </div>
            </div>
          ) : null}
          {performance.fills?.length ? (
            <details>
              <summary>Fills · {performance.fills.length}</summary>
              <DataTable
                columns={[
                  { key: "transaction_time", label: "Time" },
                  { key: "side", label: "Side" },
                  { key: "qty", label: "Shares" },
                  { key: "price", label: "Price" },
                ]}
                rows={performance.fills}
                rowKey={(r) => String(r.id)}
              />
            </details>
          ) : null}
        </div>
        {detail ? (
          <ActivityRows events={detail.activity} onSelect={() => {}} />
        ) : null}
      </section>
    </dialog>
  );
}

type OrderReview = {
  ticket_id: string;
  arguments_hash: string;
  expires_at: string;
  intent: string;
  order: Record<string, string>;
  risk: Record<string, unknown>;
};
export function PaperOrderReview({
  candidate,
  initialPolicy,
  onClose,
  onSubmitted,
}: {
  candidate: OrderAction;
  initialPolicy?: RiskPolicy;
  onClose: () => void;
  onSubmitted: (order: Record<string, string>) => Promise<void>;
}) {
  const [review, setReview] = useState<OrderReview | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  const intent = candidate.intent || "entry";
  const calculate = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const { ticker, ...body } = candidate;
      void ticker;
      const r = await secureMutation("/api/trading-agent/paper/review", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!r.ok)
        throw new Error(await responseError(r, "Order review unavailable."));
      setReview(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review unavailable.");
    } finally {
      setBusy(false);
    }
  }, [candidate]);
  useEffect(() => {
    const timer = window.setTimeout(() => void calculate(), 0);
    return () => window.clearTimeout(timer);
  }, [calculate]);
  async function confirm() {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      const r = await secureMutation("/api/trading-agent/paper/confirm", {
        method: "POST",
        body: JSON.stringify({
          ticket_id: review.ticket_id,
          arguments_hash: review.arguments_hash,
        }),
      });
      if (!r.ok)
        throw new Error(await responseError(r, "Submission unavailable."));
      const receipt = await r.json();
      await onSubmitted(receipt.order);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission unavailable.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className={styles.overlay}
      aria-label={`Review ${candidate.ticker}`}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <section className={`workbook-panel ${styles.review}`}>
        <header className="panel-header">
          <h2>
            {candidate.ticker} ·{" "}
            {intent === "entry"
              ? "Approve call"
              : intent === "cancel"
                ? "Cancel order"
                : `${intent} position`}
          </h2>
          <button className="quiet-button" onClick={onClose} disabled={busy}>
            Close
          </button>
        </header>
        {error ? (
          <p className="subtle-error" role="alert">
            {error}
          </p>
        ) : null}
        {busy && !review ? <p>Preparing order review…</p> : null}
        {review ? (
          <>
            <div className={styles.body}>
              <h3>
                {intent === "cancel"
                  ? "Request cancellation"
                  : `${review.order.side === "buy" ? "Buy" : intent === "entry" ? "Short" : "Sell"} ${review.order.qty} shares`}
              </h3>
              <p>
                {review.order.symbol} · {review.order.type} ·{" "}
                {review.order.time_in_force}
              </p>
              {intent === "cancel" ? (
                <p>
                  {review.order.filled_qty || "0"} shares already filled ·{" "}
                  {review.order.status.replaceAll("_", " ")}
                </p>
              ) : null}
            </div>
            {intent !== "cancel" ? (
              <MetricStrip
                items={[
                  {
                    label: "Estimated amount",
                    value: money(review.risk.suggested_notional),
                  },
                  {
                    label: "Portfolio volatility",
                    value: `${pct(numeric(review.risk.portfolio_volatility_before_pct))} → ${pct(numeric(review.risk.portfolio_volatility_after_pct))}`,
                  },
                ]}
              />
            ) : null}
            {review.risk.risk_error ? (
              <p role="status">{String(review.risk.risk_error)}</p>
            ) : null}
            <p>
              Review expires {new Date(review.expires_at).toLocaleTimeString()}
            </p>
            <button
              className="primary-button"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy
                ? "Submitting…"
                : intent === "cancel"
                  ? "Request cancellation"
                  : "Confirm paper order"}
            </button>
            <button
              className="quiet-button"
              disabled={busy}
              onClick={() => void calculate()}
            >
              Refresh review
            </button>
          </>
        ) : !busy ? (
          <button className="primary-button" onClick={() => void calculate()}>
            Retry review
          </button>
        ) : null}
        {initialPolicy && intent === "entry" ? (
          <details className={styles.body}>
            <summary>Sizing limits</summary>
            <p>Ledoit–Wolf covariance · annualized volatility</p>
            {Object.entries(riskLabels).map(([key, label]) => (
              <p key={key}>
                {label}: {initialPolicy[key as keyof RiskPolicy]}
              </p>
            ))}
          </details>
        ) : null}
      </section>
    </dialog>
  );
}
export function ApprovedCallDetail({ call }: { call: TrackedPaperCall }) {
  const [detail, setDetail] = useState<TrackedPaperCall | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void fetch(
      `/api/trading-agent/approved-calls/${encodeURIComponent(call.tracking_id)}`,
      {
        cache: "no-store",
        credentials: "same-origin",
        signal: controller.signal,
      },
    )
      .then(async (response) => {
        if (!response.ok)
          throw new Error(
            await responseError(response, "Call tracking is unavailable."),
          );
        const next = (await response.json()) as TrackedPaperCall;
        if (!controller.signal.aborted) {
          setDetail(next);
          setError("");
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Call tracking is unavailable.",
          );
      });
    return () => controller.abort();
  }, [call.tracking_id, call.as_of]);
  const current = detail?.tracking_id === call.tracking_id ? detail : call;
  const path = current.path || [];
  const benchmark = current.stance === "short" ? "SPY short" : "SPY long";
  return (
    <section className="workbook-panel">
      <header className="panel-header">
        <h2>
          {current.ticker} · {current.stance} · {current.horizon_label}
        </h2>
      </header>
      {error ? (
        <p className="subtle-error" role="alert">
          {error}
        </p>
      ) : null}
      <MetricStrip
        items={[
          { label: "Return", value: pct(current.return_pct) },
          { label: `Vs ${benchmark}`, value: pct(current.excess_return_pct) },
          { label: "Drawdown", value: pct(current.max_drawdown_pct) },
        ]}
      />
      {current.status === "pending_entry" ? (
        <p>Entry at the next trading day’s close.</p>
      ) : null}
      {current.status === "price_unavailable" ? (
        <p>Price data is unavailable.</p>
      ) : null}
      {current.path_status === "unavailable" ? (
        <p>Price history is unavailable.</p>
      ) : null}
      {path.length ? (
        <DataPlot
          id={`tracked-${current.tracking_id}`}
          title={`${current.ticker} · paper return`}
          data={[
            {
              type: "scatter",
              mode: path.length === 1 ? "markers" : "lines",
              name: "Call",
              x: path.map((p) => p.date),
              y: path.map((p) => p.return_pct),
              line: { color: teal },
            },
            {
              type: "scatter",
              mode: "lines",
              name: benchmark,
              x: path.map((p) => p.date),
              y: path.map((p) => p.benchmark_return_pct),
              connectgaps: false,
              line: { color: violet, dash: "dot" },
            },
          ]}
          layout={{
            xaxis: { type: "date" },
            yaxis: {
              title: { text: "Return (%)" },
              zeroline: true,
              ticksuffix: "%",
            },
            showlegend: true,
          }}
          rowCount={path.length}
        />
      ) : null}
      {current.entry_date ? (
        <p className={styles.method}>
          Entry {day(current.entry_date)} · Target{" "}
          {day(current.target_date || "")} · Prices through{" "}
          {day(current.last_price_date || "")}
        </p>
      ) : null}
      {current.hypothesis ? <p>{current.hypothesis}</p> : null}
    </section>
  );
}
