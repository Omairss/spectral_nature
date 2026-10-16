"use client";

import { useState } from "react";
import type { HomeTickerSnapshot, TrendPoint } from "@/lib/types";

const price = (value: number) => value.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dateLabel = (date: string) => new Date(date).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export function PriceContextChart({ symbol, daily, intraday }: {
  symbol: string;
  daily: TrendPoint[];
  intraday?: HomeTickerSnapshot["intraday_price_history"];
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const valid = (point: TrendPoint) => Number.isFinite(point.value) && Number.isFinite(Date.parse(point.date));
  const history = daily.filter(valid).sort((a, b) => Date.parse(a.date) - Date.parse(b.date));
  const session = (intraday?.points || []).filter(valid).sort((a, b) => Date.parse(a.date) - Date.parse(b.date));
  const sessionDate = intraday?.session || session[0]?.date.slice(0, 10);
  const end = session.length ? Date.parse(`${sessionDate}T00:00:00Z`) : Date.parse(history.at(-1)?.date || "");
  const week = history.filter(point => Date.parse(point.date) >= end - 7 * 86_400_000 && (session.length ? Date.parse(point.date) < end : Date.parse(point.date) <= end));
  const split = Boolean(week.length && session.length);
  const weekStart = Date.parse(week[0]?.date || ""), weekEnd = Date.parse(week.at(-1)?.date || "");
  const dayStart = Date.parse(session[0]?.date || ""), dayEnd = Date.parse(session.at(-1)?.date || "");
  const weekRight = split ? 148 : 306, dayLeft = split ? 172 : 52;
  const weekPoints = week.map(point => ({ ...point, intraday: false, x: week.length === 1 ? (52 + weekRight) / 2 : 52 + (Date.parse(point.date) - weekStart) / (weekEnd - weekStart || 1) * (weekRight - 52) }));
  const dayPoints = session.map(point => ({ ...point, intraday: true, x: session.length === 1 ? (dayLeft + 306) / 2 : dayLeft + (Date.parse(point.date) - dayStart) / (dayEnd - dayStart || 1) * (306 - dayLeft) }));
  const points = [...weekPoints, ...dayPoints];
  if (!points.length) return null;
  const index = Math.min(selected ?? points.length - 1, points.length - 1);
  const point = points[index];
  const low = Math.min(...points.map(point => point.value)), high = Math.max(...points.map(point => point.value));
  const span = high - low || Math.max(Math.abs(high) * 0.01, 0.01);
  const minimum = low - span * 0.08, maximum = high + span * 0.08;
  const y = (value: number) => 12 + (maximum - value) / (maximum - minimum) * 114;
  const path = (series: typeof points) => series.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
  const inspectedDate = point.intraday
    ? `${dateLabel(point.date)} · ${new Date(point.date).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })} ET`
    : `${dateLabel(point.date)} · close`;
  const weekLabel = week.length > 1 ? `${dateLabel(week[0].date)} – ${dateLabel(week.at(-1)!.date)}` : week.length ? dateLabel(week[0].date) : "";
  const dayLabel = session.length ? `${dateLabel(session[0].date)} · intraday` : "";

  return <figure className="trend-chart attention-price-chart" aria-label={`${symbol} ${split ? "weekly closes and latest intraday prices" : session.length ? "latest intraday prices" : "weekly closing prices"}`}>
    <figcaption><span>{selected === null ? split ? "Week + latest day" : session.length ? "Latest day" : "Past week" : inspectedDate}</span>{selected !== null ? <strong>{price(point.value)}</strong> : null}</figcaption>
    <svg viewBox="0 0 320 156" role="img" tabIndex={0}
      aria-label={`${symbol} price chart; use left and right arrow keys to inspect prices`}
      style={{ touchAction: "pan-y" }} onPointerLeave={() => setSelected(null)} onBlur={() => setSelected(null)}
      onPointerMove={event => {
        const rect = event.currentTarget.getBoundingClientRect();
        const px = (event.clientX - rect.left) / rect.width * 320;
        setSelected(points.reduce((best, point, index) => Math.abs(point.x - px) < Math.abs(points[best].x - px) ? index : best, 0));
      }} onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          setSelected(event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : Math.max(0, Math.min(points.length - 1, index + (event.key === "ArrowLeft" ? -1 : 1))));
        }
      }}>
      <title>{`${symbol}: ${week.length ? `daily closes ${weekLabel}` : ""}${split ? "; " : ""}${dayLabel}. ${split ? "The latest session is expanded on the right; both sections share the same price scale." : ""}`}</title>
      {split ? <><rect x={dayLeft - 6} y="6" width={312 - dayLeft} height="126" fill="currentColor" opacity="0.04" /><line x1="160" x2="160" y1="6" y2="132" stroke="currentColor" opacity="0.25" strokeDasharray="2 4" /></> : null}
      {[maximum, (maximum + minimum) / 2, minimum].map(value => <g key={value}><line x1="52" x2="306" y1={y(value)} y2={y(value)} stroke="currentColor" opacity="0.1" /><text x="46" y={y(value) + 3} textAnchor="end" fill="currentColor" opacity="0.65" fontSize="9">{price(value)}</text></g>)}
      {weekPoints.length ? <g opacity={split ? 0.5 : 1}><path d={path(weekPoints)} className="trend-line" fill="none" vectorEffect="non-scaling-stroke" />{weekPoints.map(point => <circle key={point.date} cx={point.x} cy={y(point.value)} r="2" className="trend-point" />)}</g> : null}
      {dayPoints.length ? <path d={path(dayPoints)} className="trend-line" fill="none" vectorEffect="non-scaling-stroke" /> : null}
      {selected !== null ? <line x1={point.x} x2={point.x} y1="6" y2="132" stroke="currentColor" opacity="0.35" /> : null}
      <circle cx={point.x} cy={y(point.value)} r="3" className="trend-endpoint" />
      {weekPoints.length ? <text x={(52 + weekRight) / 2} y="148" textAnchor="middle" fill="currentColor" opacity="0.7" fontSize="9">{weekLabel}</text> : null}
      {dayPoints.length ? <text x={(dayLeft + 306) / 2} y="148" textAnchor="middle" fill="currentColor" opacity="0.7" fontSize="9">{dayLabel}</text> : null}
    </svg>
  </figure>;
}
