import type { TrendPoint } from "@/lib/types";

function displayValue(value: number, unit: string, price = false) {
  if (price) return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
  if (unit === "USD") return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", minimumFractionDigits: 0, maximumFractionDigits: 1 }).format(value);
  return `${new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value)}${unit === "%" ? "%" : ""}`;
}

export function TrendChart({ points, label, unit = "USD", compact = false, tone = "teal", price = false, value }: { points: TrendPoint[]; label: string; unit?: string; compact?: boolean; tone?: string; price?: boolean; value?: number | null }) {
  const series = points.filter((point) => Number.isFinite(point.value) && Number.isFinite(Date.parse(point.date)));
  if (!series.length) return null;
  const first = series[0]; const latest = series.at(-1)!;
  const width = 320; const height = compact ? 64 : 94; const padding = 6;
  const times = series.map((point) => Date.parse(point.date));
  const minimum = Math.min(...series.map((point) => point.value)); const maximum = Math.max(...series.map((point) => point.value));
  const spread = maximum - minimum || Math.max(Math.abs(maximum) * 0.04, 1);
  const low = minimum - spread * 0.08; const high = maximum + spread * 0.08;
  const x = (index: number) => padding + ((times[index] - times[0]) / (times.at(-1)! - times[0] || 1)) * (width - padding * 2);
  const y = (value: number) => padding + (1 - (value - low) / (high - low)) * (height - padding * 2);
  const path = series.map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(2)},${y(point.value).toFixed(2)}`).join(" ");
  const dateLabel = (point: TrendPoint) => point.period || new Date(`${point.date}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit", timeZone: "UTC" });
  return <figure className={`trend-chart trend-${tone}${compact ? " compact" : ""}`} aria-label={`${label}, ${dateLabel(first)} to ${dateLabel(latest)}`}>
    <figcaption><span>{label}</span><strong>{displayValue(value ?? latest.value, unit, price)}</strong></figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label} trend`} preserveAspectRatio="none">
      <title>{`${label}: ${dateLabel(first)} ${displayValue(first.value, unit, price)} → ${dateLabel(latest)} ${displayValue(latest.value, unit, price)}`}</title>
      {low < 0 && high > 0 ? <line className="trend-zero" x1={padding} x2={width - padding} y1={y(0)} y2={y(0)} /> : null}
      <path d={path} className="trend-line" fill="none" vectorEffect="non-scaling-stroke" />
      {series.length <= 24 ? series.map((point, index) => <circle className="trend-point" cx={x(index)} cy={y(point.value)} r={2.5} key={`${point.date}-${index}`}><title>{`${point.period || point.date} · ${displayValue(point.value, unit)}`}</title></circle>) : null}
      <circle className="trend-endpoint" cx={x(series.length - 1)} cy={y(latest.value)} r={3} />
    </svg>
    <div className="trend-range"><span>{dateLabel(first)}</span><span>{dateLabel(latest)}{unit && unit !== "USD" && unit !== "%" ? ` · ${unit}` : ""}</span></div>
  </figure>;
}
