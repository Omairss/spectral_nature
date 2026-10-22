import Charts
import SwiftUI

struct TradingView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.scenePhase) private var scenePhase
  @State private var visible = false
  @State private var paper: Record = [:]
  @State private var refreshing = false
  @StateObject private var resource = Resource<JSONValue>()
  @State private var horizon = ""
  @State private var section = "portfolio"
  @State private var selectedCandidate: Record?
  @State private var decisionError = ""
  @State private var paperNotice = ""
  @State private var deciding = false
  private var horizons: [Record] { resource.value?["horizons"].records ?? [] }
  private var selected: Record {
    horizons.first { $0.text("key") == horizon } ?? horizons.first ?? [:]
  }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        ResourceStatus(resource: resource) { Task { await load() } }
        if !decisionError.isEmpty { Failure(message: decisionError) }
        if !paperNotice.isEmpty { Text(paperNotice).font(.subheadline).foregroundStyle(Theme.teal) }
        Picker("Trading view", selection: $section) {
          Text("Paper portfolio").tag("portfolio")
          Text("Calls").tag("calls")
        }.pickerStyle(.segmented)
        if section == "portfolio" {
          if resource.value != nil {
            PaperPortfolioView(portfolio: paper, calls: resource.value?["tracked_calls"].records ?? [],
              refreshing: refreshing, refresh: { Task { await refreshPaper() } }, reviewCalls: { section = "calls" })
          }
        } else if resource.value?["status"].string == "available" {
          Picker(
            "Horizon", selection: Binding(get: { selected.text("key") }, set: { horizon = $0 })
          ) {
            ForEach(Array(horizons.enumerated()), id: \.offset) { _, row in
              Text(row.text("label")).tag(row.text("key"))
            }
          }
          if !selected.text("regime_read").isEmpty {
            MarkdownView(text: selected.text("regime_read"))
          }
          if !selected.text("portfolio_posture").isEmpty {
            Panel { DisclosureGroup("Portfolio posture") { MarkdownView(text: selected.text("portfolio_posture")).padding(.top, 10) } }
          }
          if selected.text("status") == "error" {
            Text("Research did not complete for this period").font(.subheadline).foregroundStyle(.secondary)
          }
          ForEach(selected.rows("candidates").map { TradingRow(record: $0, key: "candidate_id") }) { item in
            let candidate = item.record
            TradingCandidateCard(candidate: candidate, horizon: selected, deciding: deciding,
              approve: { selectedCandidate = candidate }, reject: { Task { await decide(candidate, "reject") } })
          }
          if let history = resource.value?["decisions"].records, !history.isEmpty {
            Panel("Decisions") {
              WorkbookTable(
                rows: history,
                columns: [
                  .init(key: "ticker", title: "Symbol"), .init(key: "action", title: "Decision"),
                  .init(key: "created_at_utc", title: "Time"),
                  .init(key: "horizon_key", title: "Horizon"),
                  .init(key: "requested_email", title: "Reviewed by"),
                ])
            }
          }
        } else if resource.value != nil {
          Text("No Trading Agent report available").font(.subheadline).foregroundStyle(.secondary)
        }
      }.padding(20)
    }.navigationTitle("Trading Agent").navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext(
        "Trading Agent; horizon=\(selected.text("label")); run_id=\(resource.value?["run_id"].string ?? "")"
      )
      .task { await load() }.refreshable { await load() }
      .onAppear { visible = true }.onDisappear { visible = false }
      .task(id: "\(visible)|\(scenePhase == .active)|\(section)|\(paper.text("status"))") {
        while visible && scenePhase == .active && section == "portfolio" {
          if ["available", "partial", "pending"].contains(paper.text("status")) { await refreshPaper() }
          do { try await Task.sleep(for: .seconds(60)) } catch { return }
        }
      }
      .sheet(isPresented: Binding(get: { selectedCandidate != nil }, set: { if !$0 { selectedCandidate = nil } })) {
        if let candidate = selectedCandidate {
          PaperOrderReviewView(candidate: candidate, initialPolicy: paper.object("risk_policy")) { receipt in
            paperNotice = "Paper order submitted: \(receipt.text("side")) \(receipt.text("qty")) \(receipt.text("symbol"))"
            selectedCandidate = nil
            await load()
            section = "portfolio"
            await refreshPaper()
          }
        }
      }
  }
  private func load() async {
    await resource.load { try await session.api.get("/api/trading-agent") }
    if let value = resource.value?["paper_portfolio"].dictionaryValue { paper = value }
    if horizon.isEmpty { horizon = horizons.first?.text("key") ?? "" }
  }
  private func refreshPaper() async {
    guard !refreshing else { return }
    refreshing = true
    do {
      let result: JSONValue = try await session.api.post("/api/trading-agent/paper/refresh")
      guard let account = result.dictionaryValue else { throw APIError.invalidResponse }
      paper = account
      decisionError = ""
    } catch { decisionError = error.localizedDescription }
    refreshing = false
  }
  private func decide(_ candidate: Record, _ action: String) async {
    deciding = true
    decisionError = ""
    do {
      let _: JSONValue = try await session.api.post(
        "/api/trading-agent/decisions",
        body: ["candidate_id": .string(candidate.text("candidate_id")), "action": .string(action)])
      await load()
      if action == "approve" { section = "portfolio" }
    } catch { decisionError = error.localizedDescription }
    deciding = false
    selectedCandidate = nil
  }
}


private struct TradingCandidateCard: View {
  let candidate: Record
  let horizon: Record
  let deciding: Bool
  let approve: () -> Void
  let reject: () -> Void
  @State private var expanded = false
  private var classification: String {
    let stance = candidate.text("stance")
    if ["long", "short"].contains(stance) { return stance.capitalized }
    return candidate.text("research_status").replacingOccurrences(of: "_", with: " ").capitalized
  }
  var body: some View {
    Panel {
      DisclosureGroup(isExpanded: $expanded) {
        if expanded {
          VStack(alignment: .leading, spacing: 14) {
            NavigationLink("Company details") { InvestigatorView(ticker: candidate.text("ticker")) }
              .font(.caption).accessibilityLabel("Open \(candidate.text("ticker")) details")
            Text(candidate.text("setup")).font(.subheadline.weight(.medium))
              let market = candidate.object("market")
              Metrics(items: [
                ("1D", Format.percent(market.number("daily_change_pct"))),
                (
                  horizon.text("label"),
                  Format.percent(market.number(horizon.text("selected_horizon_col")))
                ), ("Confidence", candidate.text("confidence")),
                ("Horizon", candidate.text("suggested_horizon")),
              ])
              let spark = market["sparkline_3m"]?.array ?? []
              if !spark.isEmpty {
                Chart(Array(spark.enumerated()), id: \.offset) { index, value in
                  if let value = value.number {
                    LineMark(x: .value("Observation", index), y: .value("Price", value))
                      .foregroundStyle(Theme.teal)
                  }
                }.chartXAxis(.hidden).chartYScale(domain: .automatic(includesZero: false)).frame(
                  height: 90)
              }
              MarkdownView(text: candidate.text("hypothesis"))
              let forecast = candidate.object("forecast")
              if !forecast.isEmpty {
                if let price = forecast.number("reference_price") {
                  Text("Reference \(Format.price(price)) · snapshot \(forecast.text("reference_snapshot_as_of").prefix(10))")
                    .font(.caption).foregroundStyle(.secondary)
                }
                Text("At this price").font(.caption.weight(.semibold))
                MarkdownView(text: forecast.text("pricing_assessment"))
                let assumptions = forecast["assumptions"]?.array.map(\.string) ?? []
                if !assumptions.isEmpty {
                  DisclosureGroup("Assumptions") {
                    ForEach(Array(assumptions.enumerated()), id: \.offset) { _, value in
                      Text(value).font(.caption).padding(.vertical, 4)
                    }
                  }.font(.subheadline)
                }
                Text("Opposing case").font(.caption.weight(.semibold))
                MarkdownView(text: forecast.text("opposing_case"))
              }
              if !candidate.text("invalidation").isEmpty {
                Text("Invalidation").font(.caption.weight(.semibold))
                MarkdownView(text: candidate.text("invalidation"))
              }
              ForEach(
                [
                  ("evidence", "Evidence"), ("tail_risks", "Risks"),
                  ("evidence_limits", "Research next"),
                ], id: \.0
              ) { key, label in
                let values = candidate[key]?.array.map(\.string) ?? []
                if !values.isEmpty {
                  DisclosureGroup(label) {
                    ForEach(Array(values.enumerated()), id: \.offset) { _, value in
                      Text(value).font(.caption).frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                    }
                  }.font(.subheadline)
                }
              }
              let decision = candidate.object("decision").text("action")
              if !decision.isEmpty {
                Text(["approved", "place_requested"].contains(decision) ? "Approved" : decision.capitalized).font(.caption)
                  .foregroundStyle(Theme.teal)
              } else {
                HStack {
                  Button("Reject", role: .destructive) {
                    reject()
                  }.buttonStyle(.bordered)
                  Spacer()
                  Button("Review paper trade") { approve() }.buttonStyle(
                    .borderedProminent).disabled(!["long", "short"].contains(candidate.text("stance")))
                }.disabled(deciding)
              }
          }.padding(.top, 16)
        }
      } label: {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text(candidate.text("ticker")).font(.headline)
            Spacer()
            Text(classification).font(.caption).foregroundStyle(Theme.violet)
          }
          if !candidate.object("market").text("company_name").isEmpty {
            Text(candidate.object("market").text("company_name")).font(.caption).foregroundStyle(.secondary)
          }
          if !expanded {
            Text(candidate.text("setup")).font(.subheadline).foregroundStyle(Theme.text).lineLimit(2)
          }
        }.multilineTextAlignment(.leading).padding(.vertical, 4)
      }
    }
  }
}


private struct TradingRow: Identifiable {
  let record: Record
  let key: String
  var id: String { record.text(key) }
}

private struct ApprovedCallDetailView: View {
  @EnvironmentObject private var session: AppSession
  let chosen: Record
  @StateObject private var detail = Resource<JSONValue>()
  private var identity: String { chosen.text("tracking_id") }
  private var current: Record {
    let value = detail.value?.dictionaryValue ?? [:]
    return value.text("tracking_id") == identity ? value : chosen
  }
  private var benchmark: String { current.text("stance") == "short" ? "SPY short" : "SPY long" }
  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Text("\(chosen.text("ticker")) · \(chosen.text("stance")) · \(chosen.text("horizon_label"))")
        .font(.title3.weight(.semibold))
        ResourceStatus(resource: detail) { Task { await load() } }
        Panel {
          Metrics(items: [("Return", Format.percent(current.number("return_pct"))),
            ("Vs \(benchmark)", Format.percent(current.number("excess_return_pct"))),
            ("Drawdown", Format.percent(current.number("max_drawdown_pct")))])
          if current.text("status") == "pending_entry" {
            Text("Entry at the next trading day's close.").font(.subheadline).foregroundStyle(.secondary)
          }
          if current.text("status") == "price_unavailable" {
            Text("Price data is unavailable.").font(.subheadline).foregroundStyle(.secondary)
          }
          if current.text("path_status") == "unavailable" {
            Text("Price history is unavailable.").font(.subheadline).foregroundStyle(.secondary)
          }
          let points = current.rows("path")
          if !points.isEmpty {
            PaperReturnChart(points: points, benchmark: true, seriesLabel: "Call", benchmarkLabel: benchmark)
          }
          if !current.text("entry_date").isEmpty {
            Text("Entry \(current.text("entry_date")) · Target \(current.text("target_date")) · Prices through \(current.text("last_price_date"))")
              .font(.caption).foregroundStyle(.secondary)
          }
          if !current.text("hypothesis").isEmpty { MarkdownView(text: current.text("hypothesis")) }
        }
    }.task(id: identity + chosen.text("as_of")) { await load() }
  }
  private func load() async {
    guard !identity.isEmpty else { return }
    await detail.load(key: identity) { try await session.api.get("/api/trading-agent/approved-calls/\(identity)") }
  }
}

private struct PaperPortfolioView: View {
  let portfolio: Record
  let calls: [Record]
  let refreshing: Bool
  let refresh: () -> Void
  let reviewCalls: () -> Void
  @State private var selectedCall: TradingRow?
  private var summary: Record { portfolio.object("summary") }
  private var points: [Record] { portfolio.rows("path") }
  private var available: Bool { ["available", "partial"].contains(portfolio.text("status")) }
  private var equityRange: ClosedRange<Double> {
    let values = points.compactMap { $0.number("equity") }
    guard let low = values.min(), let high = values.max() else { return 0...1 }
    let padding = low == high ? max(1, abs(low) * 0.01) : (high - low) * 0.08
    return (low - padding)...(high + padding)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Panel("Alpaca paper portfolio") {
        if portfolio.text("status") != "unlinked" {
          Button(refreshing ? "Refreshing…" : "Refresh", action: refresh).disabled(refreshing)
        }
        if available {
          Metrics(items: [("Equity", Format.money(summary.number("equity"))), ("Cash", Format.money(summary.number("cash"))),
            ("Today", Format.money(summary.number("day_pl"))), ("Buying power", Format.money(summary.number("buying_power")))])
          if portfolio.text("status") == "partial" {
            Text("Refresh incomplete. Previous observations retained.").font(.caption).foregroundStyle(.secondary)
          }
          if !points.isEmpty {
            Chart {
              ForEach(points.map { TradingRow(record: $0, key: "date") }) { item in
                if let date = Format.date(item.id), let equity = item.record.number("equity") {
                  LineMark(x: .value("Date", date), y: .value("Equity (USD)", equity)).foregroundStyle(Theme.teal)
                }
              }
            }.chartYScale(domain: equityRange)
              .chartYAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
                AxisGridLine(); AxisTick()
                AxisValueLabel { if let amount = value.as(Double.self) { Text(Format.money(amount)).font(.caption2) } }
              } }
              .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
                AxisGridLine(); AxisTick()
                AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: false)).fixedSize() } }
              } }
              .chartYAxisLabel("Account equity (USD) · 1Y").frame(height: 240)
              .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
          }
          Metrics(items: [("1Y return", Format.percent(portfolio.number("return_pct"))),
            ("Equity drawdown", Format.percent(portfolio.number("max_drawdown_pct"))),
            ("Long exposure", Format.money(summary.number("long_market_value"))),
            ("Short exposure", Format.money(summary.number("short_market_value")))])
          if let date = Format.date(portfolio.text("as_of")) {
            Text("Updated \(date.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
          }
        } else {
          Text(portfolio.text("status") == "unlinked" ? "No Alpaca paper account connected." : "Paper account data is unavailable.").font(.subheadline).foregroundStyle(.secondary)
        }
      }
      if available {
        Panel("Positions") {
          let holdings = portfolio.rows("holdings")
          if holdings.isEmpty {
            Text("No open positions.").font(.subheadline).foregroundStyle(.secondary)
            Button("Review calls", action: reviewCalls).buttonStyle(.borderedProminent)
          } else {
            ForEach(holdings.map { TradingRow(record: $0, key: "symbol") }) { item in
              DisclosureGroup {
                Metrics(items: [("Shares", item.record.text("qty")), ("Price", Format.price(item.record.number("current_price"))),
                  ("Entry", Format.price(item.record.number("avg_entry_price"))), ("Unrealized P/L", Format.money(item.record.number("unrealized_pl")))])
              } label: {
                HStack {
                  VStack(alignment: .leading) { Text(item.id).font(.headline); Text(item.record.text("side")).font(.caption).foregroundStyle(.secondary) }
                  Spacer()
                  Text(Format.money(item.record.number("market_value"))).monospacedDigit()
                }.foregroundStyle(Theme.text).padding(.vertical, 6)
              }
            }
          }
        }
        Panel {
          let orders = portfolio.rows("orders")
          DisclosureGroup("Recent paper orders · \(orders.count)") {
            if orders.isEmpty { Text("No paper orders yet.").font(.caption).foregroundStyle(.secondary) }
            ForEach(orders.map { TradingRow(record: $0, key: "id") }) { item in
              VStack(alignment: .leading, spacing: 5) {
                Text("\(item.record.text("side").capitalized) \(item.record.text("qty")) \(item.record.text("symbol"))").font(.subheadline)
                Text("\(item.record.text("status")) · Filled \(item.record.text("filled_qty")) at \(Format.price(item.record.number("filled_avg_price")))").font(.caption).foregroundStyle(.secondary)
              }.padding(.vertical, 8)
            }
          }
        }
      }
      if !calls.isEmpty {
        Panel { DisclosureGroup("Research observations · \(calls.count)") {
          ForEach(calls.map { TradingRow(record: $0, key: "tracking_id") }) { item in
            Button { selectedCall = item } label: {
              HStack {
                Text("\(item.record.text("ticker")) · \(item.record.text("stance")) · \(item.record.text("horizon_label"))")
                Spacer()
                Text(Format.percent(item.record.number("return_pct")))
              }.font(.subheadline).foregroundStyle(Theme.text).padding(.vertical, 10)
            }.buttonStyle(.plain)
          }
        } }
      }
    }.sheet(item: $selectedCall) { item in
      NavigationStack {
        ScrollView { ApprovedCallDetailView(chosen: item.record).padding(20) }
          .workbench().navigationTitle("Research observation").navigationBarTitleDisplayMode(.inline)
          .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selectedCall = nil } } }
      }.preferredColorScheme(.dark).tint(Theme.teal)
    }
  }
}

private struct PaperOrderReviewView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.dismiss) private var dismiss
  let candidate: Record
  let submitted: (Record) async -> Void
  @State private var positionRisk: Double
  @State private var portfolioRisk: Double
  @State private var maxPosition: Double
  @State private var maxGross: Double
  @State private var review: Record = [:]
  @State private var busy = false
  @State private var error = ""
  init(candidate: Record, initialPolicy: Record, submitted: @escaping (Record) async -> Void) {
    self.candidate = candidate
    self.submitted = submitted
    _positionRisk = State(initialValue: initialPolicy.number("position_volatility_pct") ?? 0)
    _portfolioRisk = State(initialValue: initialPolicy.number("portfolio_volatility_pct") ?? 0)
    _maxPosition = State(initialValue: initialPolicy.number("max_position_pct") ?? 0)
    _maxGross = State(initialValue: initialPolicy.number("max_gross_pct") ?? 0)
  }
  private var policy: Record { ["position_volatility_pct": .number(positionRisk), "portfolio_volatility_pct": .number(portfolioRisk),
    "max_position_pct": .number(maxPosition), "max_gross_pct": .number(maxGross)] }
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 18) {
          Text("Volatility budgeting · Ledoit–Wolf covariance · annualized risk").font(.caption).foregroundStyle(.secondary)
          Panel("Risk limits") {
            riskField("Position volatility budget (%)", value: $positionRisk)
            riskField("Portfolio volatility limit (%)", value: $portfolioRisk)
            riskField("Maximum position (% equity)", value: $maxPosition)
            riskField("Maximum gross exposure (%)", value: $maxGross)
          }
          if !error.isEmpty { Failure(message: error) }
          if !review.isEmpty {
            let order = review.object("order"), risk = review.object("risk")
            Panel("\(order.text("side") == "buy" ? "Buy" : "Short") \(order.text("qty")) \(order.text("symbol"))") {
              Text("\(order.text("type").capitalized) · \(order.text("time_in_force").capitalized) · \(Format.number(risk.number("execution_price_tolerance_pct"), digits: 0))% price reserve").font(.caption).foregroundStyle(.secondary)
              Metrics(items: [("Estimated amount", Format.money(risk.number("suggested_notional"))),
                ("Estimated vol before", Format.percent(risk.number("portfolio_volatility_before_pct"))),
                ("Estimated vol after", Format.percent(risk.number("portfolio_volatility_after_pct"))),
                ("Daily observations", risk.text("observations"))])
              Text("Limited by \(risk.text("binding_limit").replacingOccurrences(of: "_", with: " "))").font(.caption).foregroundStyle(.secondary)
              Text("History \(risk.text("history_start"))–\(risk.text("history_end"))").font(.caption).foregroundStyle(.secondary)
              if let expires = Format.date(review.text("expires_at")) {
                Text("Review expires \(expires.formatted(date: .omitted, time: .shortened))").font(.caption).foregroundStyle(.secondary)
              }
              Button(busy ? "Submitting…" : error.isEmpty ? "Place paper order" : "Retry confirmation") { Task { await confirm() } }.buttonStyle(.borderedProminent).disabled(busy)
              Button("Recalculate size") { Task { await calculate() } }.buttonStyle(.bordered).disabled(busy)
            }
          } else {
            Button(busy ? "Calculating…" : "Calculate size") { Task { await calculate() } }.buttonStyle(.borderedProminent).disabled(busy)
          }
        }.padding(20)
      }.workbench().navigationTitle("\(candidate.text("ticker")) · paper trade").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close") { dismiss() }.disabled(busy) } }
    }.preferredColorScheme(.dark).tint(Theme.teal)
  }
  private func riskField(_ label: String, value: Binding<Double>) -> some View {
    HStack {
      Text(label).font(.caption)
      Spacer()
      TextField(label, value: value, format: .number).keyboardType(.decimalPad)
        .multilineTextAlignment(.trailing).frame(width: 70).disabled(busy)
        .onChange(of: value.wrappedValue) { _, _ in review = [:] }
    }
  }
  private func calculate() async {
    busy = true; error = ""; review = [:]
    do {
      let value: JSONValue = try await session.api.post("/api/trading-agent/paper/review",
        body: ["candidate_id": .string(candidate.text("candidate_id")), "policy": .object(policy)])
      guard let ticket = value.dictionaryValue else { throw APIError.invalidResponse }
      review = ticket
    } catch { self.error = error.localizedDescription }
    busy = false
  }
  private func confirm() async {
    busy = true; error = ""
    do {
      let result: JSONValue = try await session.api.post("/api/trading-agent/paper/confirm",
        body: ["ticket_id": .string(review.text("ticket_id")), "arguments_hash": .string(review.text("arguments_hash"))])
      guard let receipt = result["order"].dictionaryValue else { throw APIError.invalidResponse }
      await submitted(receipt)
    } catch { self.error = error.localizedDescription }
    busy = false
  }
}

private struct PaperReturnChart: View {
  let points: [Record]
  let benchmark: Bool
  var seriesLabel = "Paper portfolio"
  var benchmarkLabel = "Matched SPY"
  private var spansYears: Bool {
    guard let start = points.first.flatMap({ Format.date($0.text("date")) }),
      let end = points.last.flatMap({ Format.date($0.text("date")) }) else { return false }
    return end.timeIntervalSince(start) > 365 * 24 * 60 * 60
  }
  @State private var selectedDate: Date?
  private var selected: Record? {
    guard let date = selectedDate else { return nil }
    return points.min { abs((Format.date($0.text("date")) ?? .distantPast).timeIntervalSince(date)) < abs((Format.date($1.text("date")) ?? .distantPast).timeIntervalSince(date)) }
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let point = selected {
        Text("\(point.text("date")) · \(Format.percent(point.number("return_pct")))")
          .font(.caption).foregroundStyle(Theme.text)
      }
      Chart {
        ForEach(points.map { TradingRow(record: $0, key: "date") }) { item in
          if let date = Format.date(item.id) {
            if let value = item.record.number("return_pct") {
              LineMark(x: .value("Date", date), y: .value("Return (%)", value), series: .value("Series", seriesLabel))
                .foregroundStyle(by: .value("Series", seriesLabel))
              if points.count == 1 {
                PointMark(x: .value("Date", date), y: .value("Return (%)", value)).foregroundStyle(Theme.teal)
              }
            }
            if benchmark, let value = item.record.number("benchmark_return_pct") {
              LineMark(x: .value("Date", date), y: .value("Return (%)", value), series: .value("Series", benchmarkLabel))
                .foregroundStyle(by: .value("Series", benchmarkLabel))
                .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
          }
        }
        RuleMark(y: .value("Zero", 0)).foregroundStyle(.secondary.opacity(0.3))
        if let point = selected, let date = Format.date(point.text("date")) {
          RuleMark(x: .value("Date", date)).foregroundStyle(.secondary.opacity(0.5))
        }
      }.chartForegroundStyleScale(domain: benchmark ? [seriesLabel, benchmarkLabel] : [seriesLabel], range: benchmark ? [Theme.teal, Theme.violet] : [Theme.teal])
        .chartXSelection(value: $selectedDate)
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
          AxisGridLine(); AxisTick()
          AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: spansYears)).fixedSize() } }
        } }
        .chartYAxisLabel("Return (%)").frame(height: 240)
        .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
    }
  }
}
