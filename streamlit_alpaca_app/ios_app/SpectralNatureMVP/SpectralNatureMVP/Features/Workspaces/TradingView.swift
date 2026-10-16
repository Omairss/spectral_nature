import Charts
import SwiftUI

struct TradingView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<JSONValue>()
  @State private var horizon = ""
  @State private var section = "calls"
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
          Text("Calls").tag("calls")
          Text("Paper performance").tag("performance")
        }.pickerStyle(.segmented)
        if section == "performance" {
          ApprovedCallPerformanceView(calls: resource.value?["tracked_calls"].records ?? [],
            unavailable: resource.value?["tracking_status"].string == "unavailable")
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
      if action == "approve" { section = "performance" }
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

private struct ApprovedCallPerformanceView: View {
  @EnvironmentObject private var session: AppSession
  let calls: [Record]
  let unavailable: Bool
  @State private var selection = ""
  @StateObject private var detail = Resource<JSONValue>()
  private var chosen: Record { calls.first { $0.text("tracking_id") == selection } ?? calls.first ?? [:] }
  private var identity: String { chosen.text("tracking_id") }
  private var current: Record {
    let value = detail.value?.dictionaryValue ?? [:]
    return value.text("tracking_id") == identity ? value : chosen
  }
  private var benchmark: String { current.text("stance") == "short" ? "SPY short" : "SPY long" }
  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      if unavailable {
        Failure(message: "Call tracking is unavailable.")
      } else if calls.isEmpty {
        Text("Approve a call to track its performance.").font(.subheadline).foregroundStyle(.secondary)
      } else {
        Metrics(items: [("Tracked calls", "\(calls.count)"),
          ("Open", "\(calls.filter { $0.text("status") == "open" }.count)"),
          ("Completed", "\(calls.filter { $0.text("status") == "matured" }.count)")])
        Picker("Approved call", selection: Binding(get: { identity }, set: { selection = $0 })) {
          ForEach(calls.map { TradingRow(record: $0, key: "tracking_id") }) { item in
            let row = item.record
            Text("\(row.text("ticker")) · \(row.text("stance")) · \(row.text("horizon_label")) · \(row.text("approved_at_utc").prefix(10))")
              .tag(item.id)
          }
        }
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
              .chartYAxisLabel("Return (%)").frame(height: 240)
          }
          if !current.text("entry_date").isEmpty {
            Text("Entry \(current.text("entry_date")) · Target \(current.text("target_date")) · Prices through \(current.text("last_price_date"))")
              .font(.caption).foregroundStyle(.secondary)
          }
          if !current.text("hypothesis").isEmpty { MarkdownView(text: current.text("hypothesis")) }
        }
        Panel("Approved calls") {
          WorkbookTable(rows: calls, columns: [.init(key: "ticker", title: "Symbol"),
            .init(key: "stance", title: "Call"), .init(key: "horizon_label", title: "Horizon"),
            .init(key: "return_pct", title: "Return (%)"), .init(key: "status", title: "Status")])
        }
      }
    }.task(id: identity + chosen.text("as_of")) { await load() }
  }
  private func load() async {
    guard !identity.isEmpty else { return }
    await detail.load(key: identity) { try await session.api.get("/api/trading-agent/calls/\(identity)") }
  }
}
