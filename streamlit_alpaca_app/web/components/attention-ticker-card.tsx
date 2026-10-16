"use client";

import Link from "next/link";
import { PriceContextChart } from "@/components/visualizations/price-context-chart";
import { TrendChart } from "@/components/visualizations/trend-chart";
import type { HomeTickerSnapshot } from "@/lib/types";

const price = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString(undefined, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value: number | null) => value == null ? "—" : value.toLocaleString(undefined, { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });

export function AttentionTickerCard({ ticker }: { ticker: HomeTickerSnapshot }) {
  const intraday = ticker.intraday_price_history?.points.length ? ticker.intraday_price_history : null;
  const change = intraday ? intraday.change_pct : ticker.price_change_1d_pct;
  const useFreeCashFlow = ticker.free_cash_flow != null || Boolean(ticker.financial_trends?.free_cash_flow?.length);
  const fundamentals = [
    { key: "revenue", label: "Revenue", value: ticker.revenue, tone: "teal" },
    { key: "net_income", label: "Net income", value: ticker.net_income, tone: "violet" },
    useFreeCashFlow
      ? { key: "free_cash_flow", label: "Free cash flow", value: ticker.free_cash_flow, tone: "blue" }
      : { key: "operating_cash_flow", label: "Operating cash flow", value: ticker.operating_cash_flow, tone: "blue" },
    { key: "total_assets", label: "Total assets", value: ticker.total_assets, tone: "teal" },
    { key: "stockholders_equity", label: "Equity", value: ticker.stockholders_equity, tone: "violet" },
  ].filter(metric => metric.value != null || ticker.financial_trends?.[metric.key]?.length);
  return <article className="attention-ticker">
    <Link className="ticker-heading" href={`/stock-investigator?ticker=${encodeURIComponent(ticker.symbol)}`}>
      <header><div><strong>{ticker.symbol}</strong>{ticker.company_name && ticker.company_name !== ticker.symbol ? <span>{ticker.company_name}</span> : null}</div>
        <div className="ticker-price"><strong>{price(intraday?.points.at(-1)?.value ?? ticker.latest_close)}</strong>
          <span className={(change || 0) >= 0 ? "positive" : "negative"}>{change == null ? "—" : `${change > 0 ? "+" : ""}${change.toFixed(2)}%`}</span>
        </div>
      </header>
    </Link>
    <PriceContextChart symbol={ticker.symbol} daily={ticker.price_trend || []} intraday={ticker.intraday_price_history} />
    {ticker.market_cap != null || ticker.price_change_1m_pct != null ? <dl>
      {ticker.market_cap != null ? <div><dt>Market cap</dt><dd>{money(ticker.market_cap)}</dd></div> : null}
      {ticker.price_change_1m_pct != null ? <div><dt>1 month</dt><dd>{ticker.price_change_1m_pct.toFixed(1)}%</dd></div> : null}
    </dl> : null}
    {fundamentals.length ? <div className="ticker-fundamentals" aria-label={`${ticker.symbol} fundamentals`}>
      {fundamentals.map(metric => ticker.financial_trends?.[metric.key]?.length
        ? <TrendChart key={metric.key} points={ticker.financial_trends[metric.key]} label={metric.label} value={metric.value} tone={metric.tone} compact />
        : <div className="ticker-fundamental-value" key={metric.key}><span>{metric.label}</span><strong>{money(metric.value ?? null)}</strong></div>)}
    </div> : null}
  </article>;
}
