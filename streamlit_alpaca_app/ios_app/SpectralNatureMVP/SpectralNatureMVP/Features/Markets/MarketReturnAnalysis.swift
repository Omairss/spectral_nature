import SwiftUI

struct MarketReturnAnalysis: View {
  let rows: [Record]
  let horizons: [MarketCatalog.Horizon]
  let horizon: MarketCatalog.Horizon
  @State private var comparisonKey = "3m"
  @State private var selectedSymbol = ""
  @AppStorage("markets.outerPointCount") private var outerPointCount = 40

  private var comparison: MarketCatalog.Horizon? {
    horizons.first { $0.key == comparisonKey && $0.key != horizon.key }
      ?? horizons.first { $0.key == "3m" && $0.key != horizon.key }
      ?? horizons.first { $0.key != horizon.key }
  }
  private var selected: Record? { rows.first { $0.text("symbol") == selectedSymbol } }

  var body: some View {
    if let comparison {
      Panel("Return comparison") {
        HStack {
          Text("Horizontal axis").font(.caption).foregroundStyle(.secondary)
          Spacer()
          Picker("Horizontal axis", selection: Binding(
            get: { comparison.key }, set: { comparisonKey = $0 }
          )) {
            ForEach(horizons.filter { $0.key != horizon.key }) { Text($0.label).tag($0.key) }
          }.pickerStyle(.menu)
        }
        let count = rows.filter { $0.number(comparison.column) != nil && $0.number(horizon.column) != nil }.count
        let maximum = max(1, min(200, count))
        let limit = min(max(1, outerPointCount), maximum)
        Stepper(value: Binding(get: { limit }, set: { outerPointCount = $0 }), in: 1...maximum, step: 5) {
          Text("Outer points: \(min(limit, count))").font(.subheadline).monospacedDigit()
        }.disabled(count < 2)
        Text("Each point is one instrument · \(count)/\(rows.count) with both returns")
          .font(.caption2).foregroundStyle(.secondary)
        ScatterPlot(
          rows: rows, x: comparison.column, y: horizon.column, label: "symbol",
          xTitle: "\(comparison.label) return", yTitle: "\(horizon.label) return",
          color: "daily_change_pct", size: "volume", sizeTitle: "shares traded", logarithmicSize: true, movement: "daily_change_pct", colorTitle: "1 day change", colorAsPercent: true,
          colorCenteredAtZero: true, colorClipOutliers: true, percentAxes: true, includeZero: true,
          labelExtremes: true, showSelectionDetails: false,
          onSelect: { selectedSymbol = $0.text("symbol") }, pointLimit: limit
        ).id("\(comparison.key)|\(horizon.key)|\(limit)")
        if let selected {
          NavigationLink {
            InvestigatorView(ticker: selected.text("symbol"))
          } label: {
            InstrumentHeading(
              symbol: selected.text("symbol"), name: selected.text("company_name"),
              price: selected.number("close"), change: selected.number("daily_change_pct"),
              changeLabel: "1D", disclosure: true, compact: true)
          }.buttonStyle(.plain)
          Metrics(items: [
            ("\(comparison.label) return", Format.percent(selected.number(comparison.column))),
            ("\(horizon.label) return", Format.percent(selected.number(horizon.column))),
          ])
          if let volume = selected.number("volume") {
            Text("Shares traded: \(Format.number(volume, digits: 0))").font(.caption).foregroundStyle(.secondary)
          }
        }
      }
      .onChange(of: comparison.key) { _, _ in selectedSymbol = "" }
      .onChange(of: horizon.key) { _, _ in selectedSymbol = "" }
      .onChange(of: outerPointCount) { _, _ in selectedSymbol = "" }
    }
    Panel("Return leaders and laggards") {
      MarketReturnRanking(rows: rows, horizon: horizon)
    }
  }
}

/// Named tails of the same return distribution shown in the scatter plot.
/// Small universes show every instrument; large universes show five at each end.
struct MarketReturnRanking: View {
  let rows: [Record]
  let horizon: MarketCatalog.Horizon
  private var ranked: [Record] {
    rows.filter { $0.number(horizon.column) != nil }.sorted {
      let a = $0.number(horizon.column) ?? 0
      let b = $1.number(horizon.column) ?? 0
      return a == b ? $0.text("symbol") < $1.text("symbol") : a > b
    }
  }
  var body: some View {
    let all = ranked
    let values = all.count <= 10 ? all : Array(all.prefix(5)) + Array(all.suffix(5))
    let returns = values.compactMap { $0.number(horizon.column) }
    let low = min(returns.min() ?? 0, 0)
    let high = max(returns.max() ?? 0, 0)
    let span = high > low ? high - low : 1
    VStack(alignment: .leading, spacing: 10) {
      Text("\(horizon.label) return · \(values.count) of \(all.count) with history")
        .font(.caption2).foregroundStyle(.secondary)
      if values.isEmpty {
        Text("No returns for this period").font(.caption).foregroundStyle(.secondary)
      } else {
        VStack(spacing: 8) {
          ForEach(values.indices, id: \.self) { index in
            let row = values[index]
            let value = row.number(horizon.column) ?? 0
            NavigationLink {
              InvestigatorView(ticker: row.text("symbol"))
            } label: {
              HStack(spacing: 10) {
                Text(row.text("symbol")).font(.caption.weight(.medium))
                  .lineLimit(1).minimumScaleFactor(0.75).frame(width: 46, alignment: .leading)
                GeometryReader { geometry in
                  let zero = CGFloat(-low / span) * geometry.size.width
                  let end = CGFloat((value - low) / span) * geometry.size.width
                  ZStack(alignment: .leading) {
                    Rectangle().fill(.white.opacity(0.3)).frame(width: 1)
                      .offset(x: zero)
                    RoundedRectangle(cornerRadius: 3).fill(Theme.tone(value))
                      .frame(width: abs(end - zero), height: 15)
                      .offset(x: min(zero, end))
                  }.frame(height: geometry.size.height)
                }.frame(height: 22).accessibilityHidden(true)
                Text(Format.percent(value)).font(.system(size: 11)).monospacedDigit()
                  .frame(width: 76, alignment: .trailing)
              }.contentShape(Rectangle())
            }.buttonStyle(.plain)
              .accessibilityLabel("\(row.text("symbol")), \(horizon.label) return \(Format.percent(value))")
          }
        }
      }
    }
  }
}
