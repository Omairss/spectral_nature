import Charts
import SwiftUI

struct PortfolioView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<PortfolioSnapshot>()
  @State private var period = "5Y"
  @State private var section = "Overview"
  @State private var assetClass = "equity"
  private let periods = ["1D", "1W", "1M", "3M", "YTD", "1Y", "5Y", "ALL"]
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        HStack {
          Picker("View", selection: $section) {
            ForEach(["Overview", "Holdings", "Risk", "Performance", "Lists"], id: \.self) {
              Text($0)
            }
          }
          Spacer()
          Picker("Period", selection: $period) { ForEach(periods, id: \.self) { Text($0) } }
        }
        ResourceStatus(resource: resource) { Task { await load() } }
        if let portfolio = resource.value {
          if portfolio.status != "available" {
            Text(
              portfolio.refresh.text("status") == "running"
                || portfolio.refresh.text("status") == "queued"
                ? "In progress" : "Portfolio unavailable"
            ).foregroundStyle(.secondary)
          } else {
            switch section {
            case "Holdings": holdings(portfolio)
            case "Risk": risk(portfolio)
            case "Performance": performance(portfolio)
            case "Lists":
              PortfolioListsView(period: period)
            default: overview(portfolio)
            }
            if let date = Format.date(portfolio.asOf) {
              Text("Updated \(date.formatted(date: .abbreviated, time: .shortened))").font(
                .caption2
              ).foregroundStyle(.secondary)
            }
          }
        }
      }.padding(20)
    }
    .navigationTitle(resource.value?.portfolio.name ?? "Portfolio").navigationBarTitleDisplayMode(
      .inline
    )
    .workbench().researchContext(
      "Portfolio; period=\(period); view=\(section); as_of=\(resource.value?.asOf ?? "")"
    )
    .task(id: period) { await load() }.refreshable { await load() }
  }
  private func load() async {
    await resource.load(key: period, clearOnFailure: true) {
      try await session.api.get("/api/portfolio", query: ["period": period])
    }
  }

  @ViewBuilder private func overview(_ p: PortfolioSnapshot) -> some View {
    Panel {
      Text(Format.money(p.summary.number("equity") ?? p.summary.number("portfolio_value"))).font(
        .system(size: 34, weight: .medium, design: .rounded)
      ).monospacedDigit().minimumScaleFactor(0.7).lineLimit(1)
      Metrics(items: [
        ("Cash", Format.money(p.summary.number("cash"))),
        ("Day P/L", Format.money(p.summary.number("day_pl"))),
        ("Buying power", Format.money(p.summary.number("buying_power"))),
        ("Unrealized P/L", Format.money(p.summary.number("total_unrealized_pl"))),
        ("Net exposure", Format.money(p.summary.number("net_exposure"))),
        ("Holdings", String(p.holdings.count)),
      ])
    }
    if !p.timeseries.isEmpty {
      Panel("Portfolio value · \(period)") {
        TimeSeriesChart(rows: p.timeseries, series: [("portfolio", "Portfolio")])
      }
    }
    let exposures = p.holdings.filter { $0.number("market_value") != nil }.sorted {
      abs($0.number("market_value") ?? 0) > abs($1.number("market_value") ?? 0)
    }
    if !exposures.isEmpty {
      Panel("Asset exposure") {
        let groups = Dictionary(
          grouping: exposures,
          by: { $0.text("asset_class").isEmpty ? "equity" : $0.text("asset_class") })
        let classes = groups.keys.sorted()
        Chart(classes, id: \.self) { kind in
          SectorMark(
            angle: .value(
              "Gross market value",
              groups[kind, default: []].reduce(0) { $0 + abs($1.number("market_value") ?? 0) }),
            innerRadius: .ratio(0.65)
          )
          .foregroundStyle(by: .value("Asset", kind.capitalized))
        }.chartForegroundStyleScale(range: [Theme.teal, Theme.violet, Theme.blue]).frame(
          height: 190)
        Metrics(
          items: classes.map { kind in
            (
              kind.capitalized,
              Format.money(
                groups[kind, default: []].reduce(0) { $0 + abs($1.number("market_value") ?? 0) })
            )
          })
      }
      Panel("Portfolio exposure") {
        TreemapChart(
          tiles: exposures.enumerated().map { index, row in
            .init(
              id: index, label: row.text("symbol"), group: row.text("asset_class"),
              weight: abs(row.number("market_value") ?? 0),
              color: row.number("unrealized_plpc").map { $0 * 100 },
              context: [row.text("symbol"), row.text("account"), row.text("description")].filter {
                !$0.isEmpty
              }.joined(separator: " · "))
          }, weightLabel: "Gross market value", colorLabel: "Unrealized return %", money: true)
      }
      Panel("Unrealized P/L contribution") {
        Chart(Array(exposures.enumerated()), id: \.offset) { index, row in
          if let value = row.number("unrealized_pl") {
            BarMark(
              x: .value("Unrealized P/L", value),
              y: .value("Position", "\(index + 1). \(row.text("symbol"))")
            )
            .foregroundStyle(Theme.tone(value))
          }
        }.frame(height: CGFloat(exposures.count * 30 + 40))
      }
    }
    if !p.accountPerformance.isEmpty {
      Panel("Accounts") {
        WorkbookTable(
          rows: p.accountPerformance,
          columns: [
            .init(key: "account", title: "Account"),
            .init(key: "current_value", title: "Current", style: .money),
            .init(key: "return_pct", title: "Return", style: .percent),
          ])
      }
    }
  }
  @ViewBuilder private func holdings(_ p: PortfolioSnapshot) -> some View {
    Picker("Asset class", selection: $assetClass) {
      Text("Equities").tag("equity")
      Text("Options").tag("option")
      Text("Futures").tag("future")
    }.pickerStyle(.segmented)
    let rows = p.holdings.filter {
      ($0.text("asset_class").isEmpty ? "equity" : $0.text("asset_class")) == assetClass
    }
    if rows.isEmpty { Text("No holdings").foregroundStyle(.secondary) }
    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
      Panel {
        NavigationLink {
          InvestigatorView(ticker: row.text("symbol"))
        } label: {
          InstrumentHeading(
            symbol: row.text("symbol"), price: row.number("current_price"),
            change: row.number("change_today").map { $0 * 100 }, priceLabel: "Price",
            disclosure: true)
        }.buttonStyle(.plain)
        if !row.text("description").isEmpty {
          Text(row.text("description")).font(.caption).foregroundStyle(.secondary)
        }
        if !row.text("account").isEmpty {
          Text(row.text("account")).font(.caption2).foregroundStyle(.secondary)
        }
        Metrics(items: [
          ("Quantity", Format.number(row.number("qty") ?? row.number("quantity"))),
          ("Market value", Format.money(row.number("market_value"))),
          ("Unrealized P/L", Format.money(row.number("unrealized_pl"))),
          ("Side", row.text("direction")),
          ("Today", Format.percent(row.number("change_today"), fraction: true)),
          ("Return", Format.percent(row.number("unrealized_plpc"), fraction: true)),
        ])
        if assetClass == "option" {
          Metrics(items: [
            ("Delta", Format.number(row.number("delta"))),
            ("Theta", Format.number(row.number("theta"))),
            ("IV", Format.percent(row.number("implied_volatility"), fraction: true)),
            ("Strategy", row.text("strategy")),
          ])
        }
      }
    }
  }
  @ViewBuilder private func risk(_ p: PortfolioSnapshot) -> some View {
    let analytics = p.equityAnalytics
    Panel("Equity risk · \(period)") {
      Metrics(items: [
        (
          "History coverage",
          Format.percent(analytics.stats.number("history_coverage"), fraction: true)
        ),
        (
          "Volatility",
          Format.percent(analytics.stats.number("portfolio_volatility"), fraction: true)
        ), ("SPY beta", Format.number(analytics.stats.number("portfolio_beta_vs_spy"))),
        ("Effective positions", Format.number(analytics.stats.number("effective_positions"))),
        ("Independent risk bets", Format.number(analytics.stats.number("effective_risk_bets"))),
      ])
    }
    let rows = analytics.positions.filter { $0.text("history_status") == "Ready" }
    if !rows.isEmpty {
      Panel("Risk and return") {
        Chart(Array(rows.enumerated()), id: \.offset) { _, row in
          if let x = row.number("annualized_volatility"), let y = row.number("period_return") {
            PointMark(
              x: .value("Annualized volatility", x * 100), y: .value("Period return", y * 100)
            ).foregroundStyle(Theme.tone(y)).symbolSize(45)
              .annotation(position: .top) { Text(row.text("symbol")).font(.system(size: 8)) }
          }
        }.chartXAxisLabel("Annualized volatility %").chartYAxisLabel("\(period) return %").frame(
          height: 250)
      }
      Panel("Positions") {
        WorkbookTable(
          rows: rows,
          columns: [
            .init(key: "symbol", title: "Symbol"),
            .init(key: "allocation", title: "Allocation", style: .fraction),
            .init(key: "period_return", title: "Return", style: .fraction),
            .init(key: "annualized_volatility", title: "Volatility", style: .fraction),
            .init(key: "max_drawdown", title: "Drawdown", style: .fraction),
            .init(key: "beta_vs_spy", title: "SPY beta", style: .number),
            .init(key: "momentum_regime", title: "Regime"),
          ])
      }
    }
    if !analytics.correlation.labels.isEmpty {
      Panel("Return correlation") {
        CorrelationGrid(labels: analytics.correlation.labels, values: analytics.correlation.values)
      }
    }
    if !analytics.components.isEmpty {
      Panel("Factor components") {
        ScatterPlot(rows: analytics.components, x: "pc1", y: "pc2", label: "symbol",
                    xTitle: "PC1 loading", yTitle: "PC2 loading")
        WorkbookTable(
          rows: analytics.components,
          columns: [
            .init(key: "symbol", title: "Symbol"), .init(key: "pc1", title: "PC1", style: .number),
            .init(key: "pc2", title: "PC2", style: .number),
          ])
      }
    }
  }
  @ViewBuilder private func performance(_ p: PortfolioSnapshot) -> some View {
    let m = p.performance.metrics
    Panel("Performance · \(period)") {
      Metrics(items: [
        ("Start", Format.money(m.number("start_value"))),
        ("Current", Format.money(m.number("current_value"))),
        ("Change", Format.money(m.number("change_value"))),
        ("Return", Format.percent(m.number("return"), fraction: true)),
        ("Max drawdown", Format.percent(m.number("max_drawdown"), fraction: true)),
        ("Volatility", Format.percent(m.number("annualized_volatility"), fraction: true)),
      ])
    }
    if !p.timeseries.isEmpty {
      Panel("Indexed to 100") {
        TimeSeriesChart(rows: indexed(p.timeseries), series: series(p.timeseries))
      }
      Panel("Drawdown") {
        TimeSeriesChart(rows: drawdown(p.timeseries), series: [("drawdown", "Drawdown %")])
      }
    }
    if !p.timeseries.isEmpty {
      Panel("Monthly returns") {
        let months = monthlyReturns(p.timeseries)
        let years = Array(Set(months.map { $0.year })).sorted()
        MatrixChart(
          columns: [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
          ], rows: years,
          values: years.map { year in
            (1...12).map { month in months.first { $0.year == year && $0.month == month }?.value }
          }, fraction: true)
      }
    }
    if !p.performance.table.isEmpty {
      Panel("Annual performance") {
        WorkbookTable(
          rows: p.performance.table,
          columns: [
            .init(key: "entity", title: "Entity"), .init(key: "year", title: "Year"),
            .init(key: "annual_return", title: "Return", style: .fraction),
            .init(key: "sharpe_ratio", title: "Sharpe", style: .number),
            .init(key: "beta_vs_spy", title: "SPY beta", style: .number),
            .init(key: "alpha_vs_spy", title: "Alpha", style: .fraction),
            .init(key: "max_drawdown", title: "Drawdown", style: .fraction),
          ])
      }
    }
  }
  private func series(_ rows: [Record]) -> [(String, String)] {
    (rows.first?.keys.filter { $0 != "timestamp" }.sorted() ?? []).map { ($0, $0.capitalized) }
  }
  private func indexed(_ rows: [Record]) -> [Record] {
    let bases = Dictionary(
      uniqueKeysWithValues: series(rows).compactMap { key, _ -> (String, Double)? in
        guard let value = rows.first(where: { $0.number(key) != nil })?.number(key), value != 0
        else { return nil }
        return (key, value)
      })
    return rows.map { row in
      var output: Record = ["timestamp": row["timestamp"] ?? .null]
      for (key, base) in bases {
        output[key] = row.number(key).map { .number($0 / base * 100) } ?? .null
      }
      return output
    }
  }
  private func drawdown(_ rows: [Record]) -> [Record] {
    var peak = -Double.infinity
    return rows.map { row in
      guard let value = row.number("portfolio") else {
        return ["timestamp": row["timestamp"] ?? .null, "drawdown": .null]
      }
      peak = max(peak, value)
      return [
        "timestamp": row["timestamp"] ?? .null,
        "drawdown": peak > 0 ? .number((value / peak - 1) * 100) : .null,
      ]
    }
  }
  private struct MonthlyReturn {
    let year: String
    let month: Int
    let value: Double?
  }
  private func monthlyReturns(_ rows: [Record]) -> [MonthlyReturn] {
    var closing: [String: Double] = [:]
    for row in rows.sorted(by: { $0.text("timestamp") < $1.text("timestamp") }) {
      guard let date = Format.date(row.text("timestamp")), let value = row.number("portfolio")
      else { continue }
      closing[String(Format.observationDay(date).prefix(7))] = value
    }
    let keys = closing.keys.sorted()
    return keys.enumerated().map { index, key in
      let previous = index > 0 ? closing[keys[index - 1]] : nil
      let previousMonth = index > 0 ? keys[index - 1] : ""
      var calendar = Calendar(identifier: .gregorian)
      calendar.timeZone = TimeZone(secondsFromGMT: 0)!
      let adjacent =
        Format.date(previousMonth + "-01").flatMap {
          calendar.date(byAdding: .month, value: 1, to: $0)
        }.map { String(Format.observationDay($0).prefix(7)) == key } ?? false
      let value = adjacent ? previous.flatMap { $0 != 0 ? (closing[key] ?? 0) / $0 - 1 : nil } : nil
      return MonthlyReturn(
        year: String(key.prefix(4)), month: Int(key.suffix(2)) ?? 1, value: value)
    }
  }

}
