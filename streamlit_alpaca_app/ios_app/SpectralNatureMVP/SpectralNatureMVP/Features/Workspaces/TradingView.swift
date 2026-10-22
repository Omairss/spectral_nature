import Charts
import SwiftUI

struct TradingView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<JSONValue>()
  @State private var horizon = ""
  @State private var section = "portfolio"
  @State private var selectedCandidate: Record?
  @State private var decisionError = ""
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
        Picker("Trading view", selection: $section) {
          Text("Paper portfolio").tag("portfolio")
          Text("Calls").tag("calls")
        }.pickerStyle(.segmented)
        if section == "portfolio" {
          if resource.value != nil {
            PaperPortfolioView(portfolio: resource.value?["paper_portfolio"].dictionaryValue ?? [:],
              calls: resource.value?["tracked_calls"].records ?? [],
              unavailable: resource.value?["tracking_status"].string == "unavailable",
              reviewCalls: { section = "calls" })
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
      .confirmationDialog(
        "Approve \(selectedCandidate?.text("ticker") ?? "candidate")?",
        isPresented: Binding(
          get: { selectedCandidate != nil }, set: { if !$0 { selectedCandidate = nil } }),
        titleVisibility: .visible
      ) {
        if let candidate = selectedCandidate {
          Button("Approve and track") { Task { await decide(candidate, "approve") } }
        }
        Button("Cancel", role: .cancel) { selectedCandidate = nil }
      }
  }
  private func load() async {
    await resource.load { try await session.api.get("/api/trading-agent") }
    if horizon.isEmpty { horizon = horizons.first?.text("key") ?? "" }
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
                  Button("Approve and track") { approve() }.buttonStyle(
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
          let points = current.rows("path")
          if !points.isEmpty {
            Chart {
              ForEach(points.map { TradingRow(record: $0, key: "date") }) { item in
                if let date = Format.date(item.record.text("date")), let value = item.record.number("return_pct") {
                  LineMark(x: .value("Date", date), y: .value("Return (%)", value), series: .value("Series", "Call"))
                    .foregroundStyle(by: .value("Series", "Call"))
                  if points.count == 1 {
                    PointMark(x: .value("Date", date), y: .value("Return (%)", value))
                      .foregroundStyle(by: .value("Series", "Call"))
                  }
                }
                if let date = Format.date(item.record.text("date")), let value = item.record.number("benchmark_return_pct") {
                  PointMark(x: .value("Date", date), y: .value("Return (%)", value))
                    .foregroundStyle(by: .value("Series", benchmark)).symbolSize(12)
                }
              }
              RuleMark(y: .value("Zero", 0)).foregroundStyle(.secondary.opacity(0.3))
            }.chartForegroundStyleScale(domain: ["Call", benchmark], range: [Theme.teal, Theme.violet])
              .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 3)) { value in
                  AxisGridLine()
                  AxisTick()
                  AxisValueLabel {
                    if let date = value.as(Date.self) { Text(Format.observationDay(date)) }
                  }
                }
              }
              .chartYAxisLabel("Return (%)").frame(height: 240)
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
  let unavailable: Bool
  let reviewCalls: () -> Void
  @State private var selectedCall: TradingRow?
  private var points: [Record] { portfolio.rows("path") }
  private var holdings: [Record] { portfolio.rows("holdings") }
  private var allocation: Record { portfolio.object("allocation") }
  private var contributors: [Record] {
    Array(portfolio.rows("attribution").sorted { abs($0.number("contribution_pct") ?? 0) > abs($1.number("contribution_pct") ?? 0) }.prefix(8))
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if unavailable || portfolio.isEmpty || portfolio.text("status") == "unavailable" {
        Failure(message: "Portfolio data is unavailable.")
      } else if portfolio.text("status") == "empty" {
        Panel("Paper portfolio") {
          Text("Approve a call to start tracking your portfolio.").font(.subheadline).foregroundStyle(.secondary)
          Button("Review calls", action: reviewCalls).buttonStyle(.borderedProminent)
        }
      } else {
        Panel("Paper portfolio") {
          Metrics(items: [((portfolio.number("unpriced_calls") ?? 0) > 0 ? "Priced-call return" : "Portfolio return", Format.percent(portfolio.number("return_pct"))),
            ("Vs matched SPY", Format.percent(portfolio.number("excess_return_pct"))),
            ("Max drawdown", Format.percent(portfolio.number("max_drawdown_pct"))),
            ("Open calls", portfolio.text("open_calls"))])
          if portfolio.text("status") == "pending" {
            Text("Entry at the next trading day's close.").font(.subheadline).foregroundStyle(.secondary)
          }
          if portfolio.text("status") == "capital_exhausted" {
            Failure(message: "Portfolio capital was exhausted; subsequent returns are unavailable.")
          }
          if !points.isEmpty {
            PaperPortfolioReturnChart(points: points, benchmark: portfolio.text("benchmark_status") == "available")
          }
          Text("Equal entry capital · time-weighted returns · cash after exit. SPY follows the same dates and long/short mix.")
            .font(.caption).foregroundStyle(.secondary)
          if !portfolio.text("last_price_date").isEmpty {
            Text("Prices through \(portfolio.text("last_price_date"))").font(.caption).foregroundStyle(.secondary)
          }
          if (portfolio.number("stale_sessions") ?? 0) > 0 {
            Text("\(portfolio.text("stale_sessions")) sessions use older marks").font(.caption).foregroundStyle(.secondary)
          }
          if portfolio.text("status") == "partial" {
            Text("\(portfolio.text("current_marks")) of \(portfolio.text("open_calls")) open calls have current marks")
              .font(.caption).foregroundStyle(.secondary)
          }
        }
        if !points.isEmpty {
          Panel((portfolio.number("unpriced_calls") ?? 0) > 0 ? "Priced exposure" : "Exposure") {
            Metrics(items: [("Long", Format.percent(allocation.number("long_pct"))),
              ("Short", Format.percent(allocation.number("short_pct"))),
              ("Net", Format.percent(allocation.number("net_pct"))),
              ("Closed-call cash", Format.percent(allocation.number("cash_pct")))])
            Chart {
              if let value = allocation.number("long_pct") {
                BarMark(x: .value("Exposure (%)", value), y: .value("Side", "Long")).foregroundStyle(Theme.teal)
              }
              if let value = allocation.number("short_pct") {
                BarMark(x: .value("Exposure (%)", -value), y: .value("Side", "Short")).foregroundStyle(Theme.coral)
              }
              RuleMark(x: .value("Zero", 0)).foregroundStyle(.secondary.opacity(0.4))
            }.chartXAxisLabel("Exposure (% of portfolio value)").frame(height: 150)
          }
        }
        if !holdings.isEmpty {
          Panel("Positions") {
            ForEach(holdings.map { TradingRow(record: $0, key: "ticker") }) { item in
              let row = item.record
              DisclosureGroup {
                if (row.number("unpriced_calls") ?? 0) > 0 {
                  Text("\(row.text("unpriced_calls")) calls awaiting prices").font(.caption).foregroundStyle(.secondary)
                }
                let ids = row["tracking_ids"]?.array.map(\.string) ?? []
                callRows(calls.filter { ids.contains($0.text("tracking_id")) })
              } label: {
                HStack(alignment: .top, spacing: 12) {
                  VStack(alignment: .leading, spacing: 5) {
                    Text(row.text("ticker")).font(.headline).foregroundStyle(Theme.text)
                    Text([(row.number("long_calls") ?? 0) > 0 ? "\(row.text("long_calls")) long" : "", (row.number("short_calls") ?? 0) > 0 ? "\(row.text("short_calls")) short" : ""].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                  }
                  Spacer(minLength: 4)
                  VStack(alignment: .trailing, spacing: 5) {
                    Text("Net \(Format.percent(row.number("net_exposure_pct")))").font(.subheadline).foregroundStyle(Theme.text)
                    Text("\(Format.number(row.number("contribution_pct"))) pp")
                      .font(.caption).foregroundStyle(Theme.tone(row.number("contribution_pct")))
                    if row.text("stance") == "mixed" {
                      Text("Gross \(Format.percent(row.number("gross_exposure_pct")))").font(.caption).foregroundStyle(.secondary)
                    }
                  }.monospacedDigit()
                }.padding(.vertical, 7)
              }
              Divider()
            }
          }
        }
        if !contributors.isEmpty {
          Panel("Return contributors") {
            Chart {
              ForEach(contributors.map { TradingRow(record: $0, key: "ticker") }) { item in
                if let value = item.record.number("contribution_pct") {
                  BarMark(x: .value("Contribution (pp)", value), y: .value("Symbol", item.id))
                    .foregroundStyle(Theme.tone(value))
                }
              }
              RuleMark(x: .value("Zero", 0)).foregroundStyle(.secondary.opacity(0.4))
            }.chartXAxisLabel("Contribution (percentage points)")
              .frame(height: CGFloat(max(180, contributors.count * 32)))
          }
        }
        let pending = calls.filter { $0.text("status") == "pending_entry" }
        if !pending.isEmpty {
          Panel { DisclosureGroup("Pending entry · \(pending.count)") { callRows(pending) } }
        }
        let closed = calls.filter { $0.text("status") == "matured" }
        if !closed.isEmpty {
          Panel { DisclosureGroup("Closed calls · \(closed.count)") { callRows(closed) } }
        }
      }
    }.sheet(item: $selectedCall) { item in
      NavigationStack {
        ScrollView { ApprovedCallDetailView(chosen: item.record).padding(20) }
          .workbench().navigationTitle("Paper call").navigationBarTitleDisplayMode(.inline)
          .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selectedCall = nil } } }
      }.preferredColorScheme(.dark).tint(Theme.teal)
    }
  }
  private func callRows(_ values: [Record]) -> some View {
    ForEach(values.map { TradingRow(record: $0, key: "tracking_id") }) { item in
      Button { selectedCall = item } label: {
        HStack(alignment: .top) {
          VStack(alignment: .leading, spacing: 5) {
            Text("\(item.record.text("ticker")) · \(item.record.text("stance")) · \(item.record.text("horizon_label"))")
              .font(.subheadline).foregroundStyle(Theme.text)
            Text("Approved \(item.record.text("approved_at_utc").prefix(10))").font(.caption).foregroundStyle(.secondary)
          }
          Spacer()
          Text(Format.percent(item.record.number("return_pct"))).font(.subheadline).monospacedDigit()
          Image(systemName: "chevron.right").font(.caption)
        }.padding(.vertical, 10)
      }.buttonStyle(.plain)
    }
  }
}

private struct PaperPortfolioReturnChart: View {
  let points: [Record]
  let benchmark: Bool
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
              LineMark(x: .value("Date", date), y: .value("Return (%)", value), series: .value("Series", "Paper portfolio"))
                .foregroundStyle(by: .value("Series", "Paper portfolio"))
              if points.count == 1 {
                PointMark(x: .value("Date", date), y: .value("Return (%)", value)).foregroundStyle(Theme.teal)
              }
            }
            if benchmark, let value = item.record.number("benchmark_return_pct") {
              LineMark(x: .value("Date", date), y: .value("Return (%)", value), series: .value("Series", "Matched SPY"))
                .foregroundStyle(by: .value("Series", "Matched SPY"))
                .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
          }
        }
        RuleMark(y: .value("Zero", 0)).foregroundStyle(.secondary.opacity(0.3))
        if let point = selected, let date = Format.date(point.text("date")) {
          RuleMark(x: .value("Date", date)).foregroundStyle(.secondary.opacity(0.5))
        }
      }.chartForegroundStyleScale(domain: benchmark ? ["Paper portfolio", "Matched SPY"] : ["Paper portfolio"], range: benchmark ? [Theme.teal, Theme.violet] : [Theme.teal])
        .chartXSelection(value: $selectedDate)
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
          AxisGridLine(); AxisTick()
          AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: false)) } }
        } }
        .chartYAxisLabel("Return (%)").frame(height: 240)
    }
  }
}
