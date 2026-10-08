import Charts
import SwiftUI

struct TradingView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<JSONValue>()
  @State private var horizon = ""
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
        if resource.value?["status"].string == "available" {
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
            Panel("Portfolio posture") { MarkdownView(text: selected.text("portfolio_posture")) }
          }
          ForEach(Array(selected.rows("candidates").enumerated()), id: \.offset) { _, candidate in
            Panel {
              HStack {
                NavigationLink(candidate.text("ticker")) {
                  InvestigatorView(ticker: candidate.text("ticker"))
                }.font(.headline)
                Spacer()
                Text(candidate.text("stance").capitalized).font(.caption).foregroundStyle(
                  Theme.violet)
              }
              let market = candidate.object("market")
              Text(market.text("company_name")).font(.caption).foregroundStyle(.secondary)
              Metrics(items: [
                ("1D", Format.percent(market.number("daily_change_pct"))),
                (
                  selected.text("label"),
                  Format.percent(market.number(selected.text("selected_horizon_col")))
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
              Text(candidate.text("setup")).font(.headline)
              MarkdownView(text: candidate.text("hypothesis"))
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
                Text(decision.replacingOccurrences(of: "_", with: " ").capitalized).font(.caption)
                  .foregroundStyle(Theme.teal)
              } else {
                HStack {
                  Button("Reject", role: .destructive) {
                    Task { await decide(candidate, "reject") }
                  }.buttonStyle(.bordered)
                  Spacer()
                  Button("Approve") { selectedCandidate = candidate }.buttonStyle(
                    .borderedProminent)
                }.disabled(deciding)
              }
            }
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
          Button("Approve") { Task { await decide(candidate, "approve") } }
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
    } catch { decisionError = error.localizedDescription }
    deciding = false
    selectedCandidate = nil
  }
}
