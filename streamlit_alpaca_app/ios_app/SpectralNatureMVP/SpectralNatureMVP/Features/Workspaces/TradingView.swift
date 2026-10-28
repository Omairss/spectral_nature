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
  @State private var direction = "all"
  @State private var selectedCandidate: Record?
  @State private var decisionError = ""
  @State private var paperNotice = ""
  @State private var deciding = false
  private var paperOrders: Bool { resource.value?["paper_portfolio"].dictionaryValue != nil }
  private var horizons: [Record] { resource.value?["horizons"].records ?? [] }
  private var selected: Record { horizons.first { $0.text("key") == horizon } ?? horizons.first ?? [:] }
  private var candidates: [Record] { selected.rows("candidates") }
  private var filtered: [Record] { candidates.filter { direction == "all" || $0.text("stance") == direction } }
  var body: some View {
    VStack(spacing: 0) {
      Picker("Trading view", selection: $section) {
        Text(paperOrders ? "Portfolio" : "Paper performance").tag("portfolio")
        Text("Calls").tag("calls")
      }.pickerStyle(.segmented).padding(.horizontal, 20).padding(.vertical, 10)
      ScrollViewReader { proxy in
        ScrollView {
          LazyVStack(alignment: .leading, spacing: section == "calls" ? 14 : 24) {
            ResourceStatus(resource: resource) { Task { await load() } }
            if !decisionError.isEmpty { Failure(message: decisionError) }
            if !paperNotice.isEmpty { Label(paperNotice, systemImage: "checkmark.circle.fill").font(.subheadline).foregroundStyle(Theme.teal) }
            if section == "portfolio" {
              if resource.value != nil {
                PaperPortfolioView(portfolio: paper, calls: resource.value?["tracked_calls"].records ?? [],
                  trackingUnavailable: resource.value?["tracking_status"].string == "unavailable", refreshing: refreshing, refresh: { Task { await refreshPaper() } }, reviewCalls: { section = "calls" })
              }
            } else if resource.value?["status"].string == "available" {
              callsHeader
              ForEach(filtered.map { TradingRow(record: $0, key: "candidate_id") }) { item in
                TradingCandidateCard(candidate: item.record, horizon: selected, reportDate: resource.value?["as_of"].string ?? "", paperOrders: paperOrders, deciding: deciding,
                  approve: { selectedCandidate = item.record }, reject: { Task { await decide(item.record, "reject") } })
              }
              if filtered.isEmpty { Text("No calls for this selection.").font(.subheadline).foregroundStyle(Theme.secondaryText) }
              if !selected.text("regime_read").isEmpty || !selected.text("portfolio_posture").isEmpty {
                TradingSurface {
                  DisclosureGroup("Market context") {
                    VStack(alignment: .leading, spacing: 18) {
                      if !selected.text("regime_read").isEmpty { MarkdownView(text: selected.text("regime_read")) }
                      if !selected.text("portfolio_posture").isEmpty { TradingNarrative(title: "Portfolio posture", text: selected.text("portfolio_posture")) }
                    }.padding(.top, 14)
                  }.font(.subheadline.weight(.medium)).tint(Theme.secondaryText)
                }
              }
              if let history = resource.value?["decisions"].records, !history.isEmpty {
                TradingSurface {
                  DisclosureGroup("Recent decisions · \(history.count)") {
                    ForEach(Array(history.enumerated()), id: \.offset) { _, row in
                      VStack(alignment: .leading, spacing: 5) {
                        HStack {
                          Text(row.text("ticker")).font(.subheadline.weight(.semibold))
                          Spacer()
                          Text(["approved", "approve", "place_requested"].contains(row.text("action")) ? "Approved call" : row.text("action").replacingOccurrences(of: "_", with: " ").capitalized)
                            .font(.caption).foregroundStyle(Theme.secondaryText)
                        }
                        HStack {
                          Text(row.text("horizon_key").uppercased())
                          Spacer()
                          if let date = Format.date(row.text("created_at_utc")) { Text(date, format: .dateTime.month(.abbreviated).day().hour().minute()) }
                        }.font(.caption2).foregroundStyle(Theme.secondaryText)
                        if !row.text("requested_email").isEmpty { Text(row.text("requested_email")).font(.caption2).foregroundStyle(Theme.secondaryText) }
                      }.padding(.vertical, 10)
                    }
                  }.font(.subheadline.weight(.medium)).tint(Theme.secondaryText)
                }
              }
            } else if resource.value != nil {
              Text("No Trading Agent report available").font(.subheadline).foregroundStyle(Theme.secondaryText)
            }
          }.padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 28).id("trading-top")
        }.refreshable { await load(); if section == "portfolio" && paperOrders { await refreshPaper() } }
          .onChange(of: section) { _, _ in proxy.scrollTo("trading-top", anchor: .top) }
      }
    }.navigationTitle("Trading").navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext("Trading Agent; horizon=\(selected.text("label")); run_id=\(resource.value?["run_id"].string ?? "")")
      .task { await load() }
      .onAppear { visible = true }.onDisappear { visible = false }
      .task(id: "\(visible)|\(scenePhase == .active)|\(section)|\(paper.text("status"))") {
        while visible && scenePhase == .active && section == "portfolio" {
          if paperOrders && ["available", "partial", "pending"].contains(paper.text("status")) { await refreshPaper() }
          do { try await Task.sleep(for: .seconds(60)) } catch { return }
        }
      }
      .sheet(isPresented: Binding(get: { selectedCandidate != nil && paperOrders }, set: { if !$0 { selectedCandidate = nil } })) {
        if let candidate = selectedCandidate {
          PaperOrderReviewView(candidate: candidate, initialPolicy: paper.object("risk_policy")) { receipt in
            paperNotice = "Paper order submitted: \(receipt.text("side")) \(receipt.text("qty")) \(receipt.text("symbol"))"
            selectedCandidate = nil
            await load(); section = "portfolio"; await refreshPaper()
          }
        }
      }
      .confirmationDialog("Approve \(selectedCandidate?.text("ticker") ?? "candidate")?",
        isPresented: Binding(get: { selectedCandidate != nil && !paperOrders }, set: { if !$0 { selectedCandidate = nil } }),
        titleVisibility: .visible) {
        if let candidate = selectedCandidate {
          Button("Approve and track") { Task { await decide(candidate, "approve") } }
        }
        Button("Cancel", role: .cancel) { selectedCandidate = nil }
      }
  }
  private var callsHeader: some View {
    VStack(alignment: .leading, spacing: 16) {
      ScrollView(.horizontal) {
        HStack(spacing: 8) {
          ForEach(Array(horizons.enumerated()), id: \.offset) { _, row in
            Button { horizon = row.text("key"); direction = "all" } label: {
              Text(row.text("key").uppercased()).font(.subheadline.weight(.semibold)).monospacedDigit()
                .padding(.horizontal, 16).padding(.vertical, 11)
                .foregroundStyle(selected.text("key") == row.text("key") ? Theme.background : Theme.secondaryText)
                .background(selected.text("key") == row.text("key") ? Theme.teal : Theme.panel, in: Capsule())
            }.buttonStyle(.plain).accessibilityLabel(row.text("label"))
              .accessibilityAddTraits(selected.text("key") == row.text("key") ? .isSelected : [])
          }
        }
      }.scrollIndicators(.hidden)
      HStack {
        Text("\(candidates.count) \(candidates.count == 1 ? "call" : "calls") · \(selected.text("label"))").font(.caption.weight(.medium))
        Spacer()
        if let date = Format.date(resource.value?["as_of"].string ?? "") {
          Text("As of " + date.formatted(.dateTime.month(.abbreviated).day())).font(.caption).foregroundStyle(Theme.secondaryText)
        }
      }
      HStack(spacing: 16) {
        ForEach([("all", "All"), ("long", "Long"), ("short", "Short"), ("watch", "Watch")], id: \.0) { key, label in
          let count = key == "all" ? candidates.count : candidates.filter { $0.text("stance") == key }.count
          if count > 0 || key == "all" {
            Button { direction = key } label: {
              HStack(spacing: 5) { Text(label); Text("\(count)").monospacedDigit() }
                .font(.caption.weight(direction == key ? .semibold : .regular))
                .foregroundStyle(direction == key ? Theme.text : Theme.secondaryText)
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) { if direction == key { Capsule().fill(Theme.teal).frame(height: 2) } }
            }.buttonStyle(.plain).accessibilityAddTraits(direction == key ? .isSelected : [])
          }
        }
      }
      if selected.text("status") == "error" { Text("Research did not complete for this period").font(.caption).foregroundStyle(Theme.coral) }
    }
  }
  private func load() async {
    let initial = resource.value == nil
    await resource.load { try await session.api.get("/api/trading-agent") }
    if initial && !paperOrders { section = "calls" }
    if let value = resource.value?["paper_portfolio"].dictionaryValue { paper = value }
    if horizon.isEmpty { horizon = horizons.first?.text("key") ?? "" }
  }
  private func refreshPaper() async {
    guard paperOrders, !refreshing else { return }
    refreshing = true
    do {
      let result: JSONValue = try await session.api.post("/api/trading-agent/paper/refresh")
      guard let account = result.dictionaryValue else { throw APIError.invalidResponse }
      paper = account; decisionError = ""
    } catch { decisionError = error.localizedDescription }
    refreshing = false
  }
  private func decide(_ candidate: Record, _ action: String) async {
    deciding = true; decisionError = ""
    do {
      let _: JSONValue = try await session.api.post("/api/trading-agent/decisions", body: ["candidate_id": .string(candidate.text("candidate_id")), "action": .string(action)])
      await load()
      if action == "approve" { section = "portfolio" }
    } catch { decisionError = error.localizedDescription }
    deciding = false; selectedCandidate = nil
  }
}

private struct TradingCandidateCard: View {
  let candidate: Record
  let horizon: Record
  let reportDate: String
  let paperOrders: Bool
  let deciding: Bool
  let approve: () -> Void
  let reject: () -> Void
  @State private var expanded = false
  private var market: Record { candidate.object("market") }
  private var forecast: Record { candidate.object("forecast") }
  private var price: Double? { market.number("close") ?? forecast.number("reference_price") }
  var body: some View {
    TradingSurface(inset: 14) {
      VStack(alignment: .leading, spacing: 16) {
        Button { expanded.toggle() } label: { summary }.buttonStyle(.plain)
          .accessibilityLabel("\(candidate.text("ticker")), \(candidate.text("stance")), \(Format.price(price)), \(candidate.text("setup"))")
          .accessibilityValue(expanded ? "Expanded" : "Collapsed")
        if expanded {
          Divider().overlay(Theme.border)
          NavigationLink { InvestigatorView(ticker: candidate.text("ticker")) } label: {
            Label("Company details", systemImage: "arrow.up.right").font(.caption.weight(.medium))
          }.accessibilityLabel("Open \(candidate.text("ticker")) details")
          if !(market["sparkline_3m"]?.array ?? []).isEmpty {
            Text("3M price history").font(.caption).foregroundStyle(Theme.secondaryText)
            TradingSparkline(values: market["sparkline_3m"]?.array.compactMap(\.number) ?? [], color: Theme.tone(market.number("daily_change_pct")))
              .frame(height: 100).accessibilityLabel("Three-month price history")
          }
          TradingNarrative(title: "Thesis", text: candidate.text("hypothesis"))
          TradingNarrative(title: "At this price", text: forecast.text("pricing_assessment"))
          TradingNarrative(title: "Opposing case", text: forecast.text("opposing_case"))
          if !candidate.text("invalidation").isEmpty {
            VStack(alignment: .leading, spacing: 8) {
              Label("Invalidation", systemImage: "flag").font(.caption.weight(.semibold)).foregroundStyle(Theme.coral)
              MarkdownView(text: candidate.text("invalidation"))
            }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
              .background(Theme.coral.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
          }
          ForEach([("assumptions", "Assumptions"), ("evidence", "Evidence"), ("tail_risks", "Risks"), ("evidence_limits", "Research next")], id: \.0) { key, label in
            let values = (key == "assumptions" ? forecast[key]?.array : candidate[key]?.array)?.map(\.string) ?? []
            if !values.isEmpty {
              DisclosureGroup(label) {
                VStack(alignment: .leading, spacing: 10) {
                  ForEach(Array(values.enumerated()), id: \.offset) { _, value in MarkdownView(text: value) }
                }.padding(.top, 12)
              }.font(.subheadline).tint(Theme.secondaryText)
            }
          }
          let decision = candidate.object("decision").text("action")
          if !decision.isEmpty {
            Label(["approved", "approve", "place_requested"].contains(decision) ? "Approved call" : decision.capitalized, systemImage: "checkmark.circle")
              .font(.caption.weight(.medium)).foregroundStyle(Theme.secondaryText)
          } else {
            HStack(spacing: 12) {
              Button("Reject", role: .destructive, action: reject).font(.subheadline).padding(.vertical, 12).padding(.horizontal, 14)
              Button(action: approve) {
                HStack { Text(paperOrders ? "Review paper trade" : "Approve and track"); Image(systemName: "arrow.right") }.frame(maxWidth: .infinity)
              }.buttonStyle(TradingPrimaryButton()).disabled(!["long", "short"].contains(candidate.text("stance")))
            }.disabled(deciding)
          }
        }
      }
    }
  }
  private var summary: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(alignment: .top, spacing: 12) {
        VStack(alignment: .leading, spacing: 5) {
          TradingIdentity(symbol: candidate.text("ticker"), stance: candidate.text("stance"), font: .title3.weight(.semibold))
          if !market.text("company_name").isEmpty { Text(market.text("company_name")).font(.caption2).foregroundStyle(Theme.secondaryText).lineLimit(1) }
        }
        Spacer(minLength: 4)
        VStack(alignment: .trailing, spacing: 4) {
          Text(Format.price(price)).font(.headline).monospacedDigit()
          if let change = market.number("daily_change_pct") {
            Text("\(Format.percent(change)) · 1D").font(.caption2).foregroundStyle(Theme.tone(change)).monospacedDigit()
          }
        }.fixedSize(horizontal: true, vertical: false)
      }
      Text(candidate.text("setup")).font(.subheadline).lineSpacing(3).lineLimit(expanded ? nil : 2)
      HStack(spacing: 10) {
        let move = market.number(horizon.text("selected_horizon_col"))
        Text("\(horizon.text("key").uppercased()) \(Format.percent(move))").foregroundStyle(Theme.tone(move))
        Text("·").foregroundStyle(Theme.border)
        Text("\(candidate.text("confidence").capitalized) confidence").foregroundStyle(Theme.secondaryText)
        Spacer(minLength: 0)
        Image(systemName: expanded ? "chevron.up" : "chevron.down").foregroundStyle(Theme.secondaryText)
      }.font(.caption2).monospacedDigit()
      if let date = Format.date(market.text("asof_time_utc").isEmpty ? forecast.text("reference_snapshot_as_of") : market.text("asof_time_utc")),
        Format.date(reportDate).map({ Format.observationDay($0) != Format.observationDay(date) }) ?? true {
        Text("Snapshot \(date.formatted(date: .abbreviated, time: .omitted))").font(.caption2).foregroundStyle(Theme.secondaryText)
      }
    }.frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
  }
}

private struct TradingSurface<Content: View>: View {
  let inset: CGFloat
  @ViewBuilder var content: () -> Content
  init(inset: CGFloat = 16, @ViewBuilder content: @escaping () -> Content) { self.inset = inset; self.content = content }
  var body: some View {
    VStack(alignment: .leading, spacing: 16, content: content)
      .padding(inset).frame(maxWidth: .infinity, alignment: .leading)
      .background(Theme.panel, in: RoundedRectangle(cornerRadius: 16))
      .overlay(RoundedRectangle(cornerRadius: 16).stroke(Theme.border.opacity(0.65)))
  }
}

private struct TradingIdentity: View {
  let symbol: String
  let stance: String
  var font = Font.headline
  var body: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 8) { Text(symbol).font(font); TradingStance(stance: stance) }
        .fixedSize(horizontal: true, vertical: false)
      VStack(alignment: .leading, spacing: 5) {
        Text(symbol).font(font).fixedSize(horizontal: false, vertical: true)
        TradingStance(stance: stance)
      }
    }
  }
}

private struct TradingStance: View {
  let stance: String
  private var color: Color { stance == "long" ? Theme.teal : stance == "short" ? Theme.coral : Theme.secondaryText }
  var body: some View {
    Label(stance.capitalized, systemImage: stance == "long" ? "arrow.up.right" : stance == "short" ? "arrow.down.right" : "eye")
      .font(.caption2.weight(.semibold)).labelStyle(.titleAndIcon).foregroundStyle(color)
      .fixedSize(horizontal: true, vertical: false)
      .padding(.horizontal, 7).padding(.vertical, 4).background(color.opacity(0.09), in: Capsule())
  }
}

private struct TradingPrimaryButton: ButtonStyle {
  @Environment(\.isEnabled) private var enabled
  func makeBody(configuration: Configuration) -> some View {
    configuration.label.font(.subheadline.weight(.semibold)).padding(.horizontal, 16).padding(.vertical, 13)
      .foregroundStyle(Theme.background).background(Theme.teal.opacity(enabled ? configuration.isPressed ? 0.75 : 1 : 0.35), in: RoundedRectangle(cornerRadius: 11))
  }
}

private struct TradingNarrative: View {
  let title: String
  let text: String
  var body: some View {
    if !text.isEmpty {
      VStack(alignment: .leading, spacing: 8) {
        Text(title).font(.caption.weight(.semibold)).foregroundStyle(Theme.secondaryText)
        MarkdownView(text: text)
      }
    }
  }
}

private struct TradingSparkline: View {
  let values: [Double]
  var color = Theme.teal
  var body: some View {
    Chart(Array(values.enumerated()), id: \.offset) { index, value in
      LineMark(x: .value("Observation", index), y: .value("Price", value)).foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 1.6))
    }.chartXAxis(.hidden).chartYAxis(.hidden).chartYScale(domain: .automatic(includesZero: false)).allowsHitTesting(false)
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
    VStack(alignment: .leading, spacing: 24) {
      HStack { Text(chosen.text("ticker")).font(.title2.weight(.semibold)); TradingStance(stance: chosen.text("stance")); Spacer(); Text(chosen.text("horizon_label")).font(.caption).foregroundStyle(Theme.secondaryText) }
      VStack(alignment: .leading, spacing: 6) {
        Text("Call return").font(.caption).foregroundStyle(Theme.secondaryText)
        Text(Format.percent(current.number("return_pct"))).font(.system(.largeTitle, design: .rounded).weight(.semibold)).foregroundStyle(Theme.tone(current.number("return_pct"))).monospacedDigit()
      }
      ResourceStatus(resource: detail) { Task { await load() } }
      if !current.rows("path").isEmpty { PaperReturnChart(points: current.rows("path"), benchmark: true, seriesLabel: "Call", benchmarkLabel: benchmark) }
      TradingSurface {
        HStack(alignment: .top, spacing: 12) {
          TradingMetric(label: benchmark, value: Format.percent(current.number("benchmark_return_pct")))
          TradingMetric(label: "Difference", value: Format.percent(current.number("excess_return_pct")), tone: Theme.tone(current.number("excess_return_pct")))
          TradingMetric(label: "Drawdown", value: Format.percent(current.number("max_drawdown_pct")))
        }
      }
      if ["pending_entry", "price_unavailable"].contains(current.text("status")) || current.text("path_status") == "unavailable" {
        Text(current.text("status") == "pending_entry" ? "Entry at the next trading day's close." : "Price history is unavailable.").font(.caption).foregroundStyle(Theme.secondaryText)
      }
      VStack(spacing: 12) {
        datedRow("Entry", raw: current.text("entry_date"))
        datedRow("Target", raw: current.text("target_date"))
        datedRow("Last observation", raw: current.text("last_price_date"))
      }
      TradingNarrative(title: "Original thesis", text: current.text("hypothesis"))
    }.task(id: identity + chosen.text("as_of")) { await load() }
  }
  @ViewBuilder private func datedRow(_ title: String, raw: String) -> some View {
    if let date = Format.date(raw) {
      HStack { Text(title).foregroundStyle(Theme.secondaryText); Spacer(); Text(date.formatted(Date.FormatStyle(date: .abbreviated, time: .omitted, timeZone: TimeZone(secondsFromGMT: 0)!))) }.font(.caption)
    }
  }
  private func load() async {
    guard !identity.isEmpty else { return }
    await detail.load(key: identity) { try await session.api.get("/api/trading-agent/approved-calls/\(identity)") }
  }
}

private struct TradingMetric: View {
  let label: String
  let value: String
  var tone = Theme.text
  var body: some View {
    VStack(alignment: .leading, spacing: 7) {
      Text(label).font(.caption).foregroundStyle(Theme.secondaryText)
      Text(value).font(.system(.body, design: .rounded).weight(.medium)).monospacedDigit().foregroundStyle(tone)
        .lineLimit(1).minimumScaleFactor(0.7)
    }.frame(maxWidth: .infinity, alignment: .leading).accessibilityElement(children: .combine)
  }
}

private struct PaperPortfolioView: View {
  let portfolio: Record
  let calls: [Record]
  let trackingUnavailable: Bool
  let refreshing: Bool
  let refresh: () -> Void
  let reviewCalls: () -> Void
  @State private var selectedCall: TradingRow?
  @State private var selectedHolding: TradingRow?
  private var summary: Record { portfolio.object("summary") }
  private var available: Bool { ["available", "partial"].contains(portfolio.text("status")) }
  private var holdings: [Record] { portfolio.rows("holdings").sorted { abs($0.number("market_value") ?? 0) > abs($1.number("market_value") ?? 0) } }
  private var orders: [Record] { portfolio.rows("orders") }
  var body: some View {
    VStack(alignment: .leading, spacing: 24) {
      if !portfolio.isEmpty {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            Label("Alpaca · Paper", systemImage: "building.columns").font(.caption.weight(.medium)).foregroundStyle(Theme.secondaryText)
            Spacer()
            if portfolio.text("status") != "unlinked" {
              Button(action: refresh) {
                if refreshing { ProgressView().controlSize(.small) } else { Image(systemName: "arrow.clockwise").font(.subheadline) }
              }.buttonStyle(.plain).frame(width: 32, height: 32).disabled(refreshing).accessibilityLabel("Refresh paper account")
            }
          }
          if available {
            Text(Format.money(summary.number("equity"))).font(.system(.largeTitle, design: .rounded).weight(.semibold)).monospacedDigit()
              .lineLimit(1).minimumScaleFactor(0.6).accessibilityLabel("Account equity \(Format.money(summary.number("equity")))")
            HStack(spacing: 6) {
              Text(Format.money(summary.number("day_pl"))).foregroundStyle(Theme.tone(summary.number("day_pl")))
              if let change = summary.number("day_return_pct") { Text("(\(Format.percent(change)))").foregroundStyle(Theme.tone(change)) }
              Text("today").foregroundStyle(Theme.secondaryText)
            }.font(.subheadline).monospacedDigit()
          } else {
            Text(portfolio.text("status") == "unlinked" ? "No paper account connected" : "Account data unavailable").font(.title3.weight(.medium))
          }
        }
        if available {
          if !portfolio.rows("path").isEmpty { PaperEquityHistory(points: portfolio.rows("path")) }
          if portfolio.text("status") == "partial" { Label("Refresh incomplete", systemImage: "clock").font(.caption).foregroundStyle(Theme.coral) }
          TradingSurface {
            LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)], alignment: .leading, spacing: 22) {
              TradingMetric(label: "Cash", value: Format.money(summary.number("cash")))
              TradingMetric(label: "Buying power", value: Format.money(summary.number("buying_power")))
              TradingMetric(label: "1Y return", value: Format.percent(portfolio.number("return_pct")), tone: Theme.tone(portfolio.number("return_pct")))
              TradingMetric(label: "Max drawdown", value: Format.percent(portfolio.number("max_drawdown_pct")))
            }
          }
          VStack(alignment: .leading, spacing: 12) {
            HStack {
              Text("Holdings").font(.headline)
              Text("\(holdings.count)").font(.caption).foregroundStyle(Theme.secondaryText)
              Spacer()
              Button("Browse calls", action: reviewCalls).font(.caption.weight(.medium))
            }
            TradingSurface {
              if holdings.isEmpty {
                HStack(spacing: 12) {
                  Image(systemName: "tray").font(.title3).foregroundStyle(Theme.secondaryText)
                  Text("No open positions").font(.subheadline).foregroundStyle(Theme.secondaryText)
                }.padding(.vertical, 5)
              } else {
                ForEach(holdings.map { TradingRow(record: $0, key: "symbol") }) { item in
                  Button { selectedHolding = item } label: {
                    HStack(spacing: 12) {
                      VStack(alignment: .leading, spacing: 5) {
                        TradingIdentity(symbol: item.id, stance: item.record.text("side"), font: .subheadline.weight(.semibold))
                        Text("\(item.record.text("qty")) shares").font(.caption).foregroundStyle(Theme.secondaryText)
                      }
                      Spacer()
                      VStack(alignment: .trailing, spacing: 5) {
                        Text(Format.money(item.record.number("market_value"))).font(.subheadline.weight(.medium)).lineLimit(1).minimumScaleFactor(0.8)
                        Text(Format.money(item.record.number("unrealized_pl"))).font(.caption).foregroundStyle(Theme.tone(item.record.number("unrealized_pl")))
                      }.monospacedDigit()
                      Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.secondaryText)
                    }.contentShape(Rectangle()).padding(.vertical, 5)
                  }.buttonStyle(.plain).accessibilityLabel("\(item.id), value \(Format.money(item.record.number("market_value"))), unrealized P/L \(Format.money(item.record.number("unrealized_pl")))")
                  if item.id != holdings.last?.text("symbol") { Divider() }
                }
              }
            }
          }
          if !holdings.isEmpty { exposure }
          TradingSurface {
            HStack { Text("Recent orders").font(.subheadline.weight(.semibold)); Spacer(); Text("\(orders.count)").font(.caption).foregroundStyle(Theme.secondaryText) }
            if orders.isEmpty { Text("No paper orders yet").font(.caption).foregroundStyle(Theme.secondaryText) }
            else {
              ForEach(orders.prefix(5).map { TradingRow(record: $0, key: "id") }) { item in orderRow(item.record) }
              if orders.count > 5 {
                DisclosureGroup("Earlier orders") { ForEach(orders.dropFirst(5).map { TradingRow(record: $0, key: "id") }) { item in orderRow(item.record) } }.font(.caption).tint(Theme.secondaryText)
              }
            }
          }
          if let date = Format.date(portfolio.text("as_of")) {
            Text("Updated \(date.formatted(date: .abbreviated, time: .shortened))").font(.caption2).foregroundStyle(Theme.secondaryText)
          }
        }
      }
      if trackingUnavailable { Failure(message: "Call tracking is unavailable.") }
      if portfolio.isEmpty {
        if let first = calls.first { ApprovedCallDetailView(chosen: first) }
        else if !trackingUnavailable { Text("No approved calls").font(.subheadline).foregroundStyle(Theme.secondaryText) }
      }
      if !calls.isEmpty {
        TradingSurface {
          DisclosureGroup {
            ForEach(calls.map { TradingRow(record: $0, key: "tracking_id") }) { item in
              Button { selectedCall = item } label: {
                HStack {
                  VStack(alignment: .leading, spacing: 5) {
                    Text(item.record.text("ticker")).font(.subheadline.weight(.semibold))
                    Text("\(item.record.text("stance").capitalized) · \(item.record.text("horizon_label"))").font(.caption).foregroundStyle(Theme.secondaryText)
                  }
                  Spacer()
                  Text(Format.percent(item.record.number("return_pct"))).font(.headline).foregroundStyle(Theme.tone(item.record.number("return_pct"))).monospacedDigit()
                  Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.secondaryText)
                }.padding(.vertical, 12).contentShape(Rectangle())
              }.buttonStyle(.plain)
            }
          } label: {
            HStack { Text(portfolio.isEmpty ? "Approved calls" : "Research history"); Spacer(); Text("\(calls.count)").foregroundStyle(Theme.secondaryText) }
          }.font(.subheadline.weight(.medium)).tint(Theme.secondaryText)
        }
      }
    }.sheet(item: $selectedCall) { item in
      NavigationStack {
        ScrollView { ApprovedCallDetailView(chosen: item.record).padding(20) }
          .workbench().navigationTitle("Research performance").navigationBarTitleDisplayMode(.inline)
          .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selectedCall = nil } } }
      }.preferredColorScheme(.dark).tint(Theme.teal)
    }.sheet(item: $selectedHolding) { item in
      NavigationStack {
        ScrollView {
          VStack(alignment: .leading, spacing: 24) {
            HStack { TradingStance(stance: item.record.text("side")); Spacer(); Text("\(item.record.text("qty")) shares").font(.caption).foregroundStyle(Theme.secondaryText) }
            VStack(alignment: .leading, spacing: 8) {
              Text("Market value").font(.caption).foregroundStyle(Theme.secondaryText)
              Text(Format.money(item.record.number("market_value"))).font(.system(.largeTitle, design: .rounded).weight(.semibold)).monospacedDigit()
            }
            TradingSurface {
              Metrics(items: [("Current price", Format.price(item.record.number("current_price"))), ("Average entry", Format.price(item.record.number("avg_entry_price"))),
                ("Unrealized P/L", Format.money(item.record.number("unrealized_pl"))), ("Return", Format.percent(item.record.number("unrealized_plpc"), fraction: true))])
            }
            NavigationLink("Company details") { InvestigatorView(ticker: item.id) }.font(.subheadline)
          }.padding(20)
        }.workbench().navigationTitle(item.id).navigationBarTitleDisplayMode(.inline)
          .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { selectedHolding = nil } } }
      }.preferredColorScheme(.dark).tint(Theme.teal)
    }
  }
  private var exposure: some View {
    let long = abs(summary.number("long_market_value") ?? 0), short = abs(summary.number("short_market_value") ?? 0)
    return TradingSurface {
      HStack { Text("Gross exposure").font(.subheadline.weight(.medium)); Spacer(); Text(Format.money(long + short)).font(.subheadline).monospacedDigit() }
      if long + short > 0 {
        GeometryReader { geometry in
          HStack(spacing: 3) {
            if long > 0 { Capsule().fill(Theme.teal).frame(width: max(0, (geometry.size.width - (short > 0 ? 3 : 0)) * long / (long + short))) }
            if short > 0 { Capsule().fill(Theme.coral) }
          }
        }.frame(height: 5).accessibilityHidden(true)
      }
      HStack {
        Label("Long \(Format.money(long))", systemImage: "arrow.up.right").foregroundStyle(Theme.teal)
        Spacer()
        Label("Short \(Format.money(short))", systemImage: "arrow.down.right").foregroundStyle(Theme.coral)
      }.font(.caption2).monospacedDigit()
    }
  }
  private func orderRow(_ row: Record) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack {
        Text(row.text("symbol")).font(.subheadline.weight(.semibold))
        Spacer()
        Text(row.text("status").replacingOccurrences(of: "_", with: " ").capitalized).font(.caption).foregroundStyle(Theme.secondaryText)
      }
      Text("\(row.text("side").capitalized) \(row.text("qty")) · \(row.text("filled_qty")) filled at \(Format.price(row.number("filled_avg_price")))")
        .font(.caption).foregroundStyle(Theme.secondaryText)
    }.padding(.vertical, 5)
  }
}

private struct PaperEquityHistory: View {
  let points: [Record]
  @State private var window = "1Y"
  @State private var selectedDate: Date?
  private var filtered: [Record] {
    guard let last = points.last.flatMap({ Format.date($0.text("date")) }) else { return [] }
    let months = window == "1M" ? 1 : window == "3M" ? 3 : 12
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0)!
    let cutoff = calendar.date(byAdding: .month, value: -months, to: last) ?? .distantPast
    return points.filter { (Format.date($0.text("date")) ?? .distantPast) >= cutoff }
  }
  private var selected: Record? {
    guard let date = selectedDate else { return nil }
    return filtered.min { abs((Format.date($0.text("date")) ?? .distantPast).timeIntervalSince(date)) < abs((Format.date($1.text("date")) ?? .distantPast).timeIntervalSince(date)) }
  }
  private var range: ClosedRange<Double> {
    let values = filtered.compactMap { $0.number("equity") }
    guard let low = values.min(), let high = values.max() else { return 0...1 }
    let pad = high == low ? max(1, abs(low) * 0.005) : (high - low) * 0.15
    return (low - pad)...(high + pad)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack {
        if let row = selected {
          Text(Format.money(row.number("equity"))).foregroundStyle(Theme.text)
          if let date = Format.date(row.text("date")) { Text(Format.historyDate(date, years: false)).foregroundStyle(Theme.secondaryText) }
        } else { Text("Account equity").foregroundStyle(Theme.secondaryText) }
        Spacer()
        Text(window).foregroundStyle(Theme.secondaryText)
      }.font(.caption).monospacedDigit()
      Chart {
        ForEach(filtered.map { TradingRow(record: $0, key: "date") }) { item in
          if let date = Format.date(item.id), let value = item.record.number("equity") {
            AreaMark(x: .value("Date", date), yStart: .value("Base", range.lowerBound), yEnd: .value("Equity", value))
              .foregroundStyle(LinearGradient(colors: [Theme.teal.opacity(0.12), Theme.teal.opacity(0)], startPoint: .top, endPoint: .bottom))
            LineMark(x: .value("Date", date), y: .value("Equity", value)).foregroundStyle(Theme.teal).lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
            if filtered.count == 1 { PointMark(x: .value("Date", date), y: .value("Equity", value)).foregroundStyle(Theme.teal) }
          }
        }
        if let row = selected, let date = Format.date(row.text("date")), let value = row.number("equity") {
          RuleMark(x: .value("Date", date)).foregroundStyle(Theme.secondaryText.opacity(0.35)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
          PointMark(x: .value("Date", date), y: .value("Equity", value)).foregroundStyle(Theme.teal)
        }
      }.chartYScale(domain: range).chartXSelection(value: $selectedDate)
        .chartYAxis { AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
          AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3])).foregroundStyle(Theme.border)
          AxisValueLabel { if let amount = value.as(Double.self) { Text(Format.money(amount, compact: true)).font(.caption2).foregroundStyle(Theme.secondaryText) } }
        } }
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
          AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: false)).font(.caption2).foregroundStyle(Theme.secondaryText) } }
        } }.frame(height: 170).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
        .accessibilityLabel("Alpaca account equity in US dollars, \(window) history")
      HStack(spacing: 8) {
        ForEach(["1M", "3M", "1Y"], id: \.self) { value in
          Button { window = value; selectedDate = nil } label: {
            Text(value).font(.caption.weight(.semibold)).frame(maxWidth: .infinity).padding(.vertical, 10)
              .foregroundStyle(window == value ? Theme.text : Theme.secondaryText)
              .background(window == value ? Theme.panel : Color.clear, in: Capsule())
          }.buttonStyle(.plain).accessibilityAddTraits(window == value ? .isSelected : [])
        }
      }
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
          HStack { TradingStance(stance: candidate.text("stance")); Spacer(); Text("Alpaca · Paper").font(.caption).foregroundStyle(Theme.secondaryText) }
          TradingSurface {
            Text("Risk limits").font(.headline)
            riskField("Position volatility budget (%)", value: $positionRisk)
            riskField("Portfolio volatility limit (%)", value: $portfolioRisk)
            riskField("Maximum position (% equity)", value: $maxPosition)
            riskField("Maximum gross exposure (%)", value: $maxGross)
          }
          if !error.isEmpty { Failure(message: error) }
          if !review.isEmpty {
            let order = review.object("order"), risk = review.object("risk")
            TradingSurface {
              Text("\(order.text("side") == "buy" ? "Buy" : "Short") \(order.text("qty")) \(order.text("symbol"))").font(.title3.weight(.semibold))
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
              Button(busy ? "Submitting…" : error.isEmpty ? "Place paper order" : "Retry confirmation") { Task { await confirm() } }.buttonStyle(TradingPrimaryButton()).disabled(busy)
              Button("Recalculate size") { Task { await calculate() } }.buttonStyle(.bordered).disabled(busy)
            }
          } else {
            Button(busy ? "Calculating…" : "Calculate size") { Task { await calculate() } }.buttonStyle(TradingPrimaryButton()).disabled(busy)
          }
          DisclosureGroup("Sizing method") {
            Text("Ledoit–Wolf covariance · annualized volatility").font(.caption).foregroundStyle(Theme.secondaryText).padding(.top, 8)
          }.font(.caption).tint(Theme.secondaryText)
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
  var seriesLabel = "Call"
  var benchmarkLabel = "SPY"
  @State private var selectedDate: Date?
  private var range: ClosedRange<Double> {
    let values = points.flatMap { row in [row.number("return_pct"), benchmark ? row.number("benchmark_return_pct") : nil].compactMap { $0 } }
    let low = min(values.min() ?? 0, 0), high = max(values.max() ?? 0, 0)
    let padding = max(0.1, (high - low) * 0.12)
    return (low - padding)...(high + padding)
  }
  private var color: Color { Theme.tone(points.last?.number("return_pct")) }
  private var selected: Record? {
    guard let date = selectedDate else { return nil }
    return points.min { abs((Format.date($0.text("date")) ?? .distantPast).timeIntervalSince(date)) < abs((Format.date($1.text("date")) ?? .distantPast).timeIntervalSince(date)) }
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 16) {
        Label(seriesLabel, systemImage: "circle.fill").foregroundStyle(color)
        if benchmark { Label(benchmarkLabel, systemImage: "minus").foregroundStyle(Theme.secondaryText) }
        Spacer()
      }.font(.caption2)
      if let row = selected {
        HStack {
          if let date = Format.date(row.text("date")) { Text(Format.historyDate(date, years: false)) }
          Spacer()
          Text(Format.percent(row.number("return_pct"))).foregroundStyle(color)
          if benchmark { Text(Format.percent(row.number("benchmark_return_pct"))).foregroundStyle(Theme.secondaryText) }
        }.font(.caption).monospacedDigit()
      }
      Chart {
        ForEach(points.map { TradingRow(record: $0, key: "date") }) { item in
          if let date = Format.date(item.id) {
            if let value = item.record.number("return_pct") {
              LineMark(x: .value("Date", date), y: .value("Return", value), series: .value("Series", seriesLabel))
                .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round))
              if points.count == 1 { PointMark(x: .value("Date", date), y: .value("Return", value)).foregroundStyle(color) }
            }
            if benchmark, let value = item.record.number("benchmark_return_pct") {
              LineMark(x: .value("Date", date), y: .value("Return", value), series: .value("Series", benchmarkLabel))
                .foregroundStyle(Theme.secondaryText).lineStyle(StrokeStyle(lineWidth: 1.3, dash: [4, 4]))
            }
          }
        }
        RuleMark(y: .value("Zero", 0)).foregroundStyle(Theme.border)
        if let row = selected, let date = Format.date(row.text("date")) {
          RuleMark(x: .value("Date", date)).foregroundStyle(Theme.secondaryText.opacity(0.4)).lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
        }
      }.chartYScale(domain: range).chartLegend(.hidden).chartXSelection(value: $selectedDate)
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 3)) { value in
          AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: false)).font(.caption2).foregroundStyle(Theme.secondaryText) } }
        } }
        .chartYAxis { AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
          AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3])).foregroundStyle(Theme.border)
          AxisValueLabel { if let number = value.as(Double.self) { Text(Format.percent(number)).font(.caption2).foregroundStyle(Theme.secondaryText) } }
        } }.frame(height: 200).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
        .accessibilityLabel("\(seriesLabel) and \(benchmarkLabel) return history in percent")
    }
  }
}
