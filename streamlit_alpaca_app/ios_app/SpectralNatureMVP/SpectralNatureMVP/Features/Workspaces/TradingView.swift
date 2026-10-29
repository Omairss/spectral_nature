import Charts
import SwiftUI

private enum TradingPresentation: Identifiable {
  case order(TradingRow), call(TradingRow), settings
  var id: String {
    switch self {
    case .order(let row): "order:" + row.id
    case .call(let row): "call:" + row.id
    case .settings: "settings"
    }
  }
}

struct TradingView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.scenePhase) private var scenePhase
  @StateObject private var resource = Resource<JSONValue>()
  @State private var period = "1M"
  @State private var section = "activity"
  @State private var visible = false
  @State private var refreshing = false
  @State private var busy = false
  @State private var error = ""
  @State private var notice = ""
  @State private var presentation: TradingPresentation?
  @State private var earlierEvents: [Record] = []
  @State private var cursor = ""
  private var paper: Record { resource.value?["paper_portfolio"].dictionaryValue ?? [:] }
  private var workspace: Record { resource.value?["workspace"].dictionaryValue ?? [:] }
  private var policy: Record { workspace.object("policy") }
  private var pending: [Record] { workspace.rows("proposals").filter { $0.text("status") == "pending" } }
  private var calls: [Record] { workspace.rows("calls").filter { !["", "not_filled"].contains($0.text("status")) } }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 24) {
        ResourceStatus(resource: resource) { Task { await load() } }
        if !error.isEmpty { Failure(message: error) }
        if !notice.isEmpty { Text(notice).font(.caption).foregroundStyle(Theme.teal) }
        if resource.value != nil {
          PaperPortfolioView(portfolio: paper, period: $period, refreshing: refreshing, refresh: { Task { await refresh() } })
          if !policy.isEmpty { controls }
          Picker("Trading", selection: $section) {
            Text("Activity").tag("activity")
            Text("Positions").tag("positions")
            Text("Performance").tag("performance")
          }.pickerStyle(.segmented)
          if section == "activity" { activity }
          else if section == "positions" { positions }
          else { performance }
        }
      }.padding(.horizontal, 20).padding(.vertical, 20)
    }.navigationTitle("Trading").navigationBarTitleDisplayMode(.inline).workbench()
      .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { presentation = .settings } label: { Image(systemName: "slider.horizontal.3") }.accessibilityLabel("Trading settings").disabled(policy.isEmpty) } }
      .researchContext("Paper Trading; period=\(period)")
      .refreshable { await refresh() }
      .task(id: period) { await load() }
      .onAppear { visible = true }.onDisappear { visible = false }
      .task(id: "\(visible)|\(scenePhase == .active)|\(paper.text("status"))") {
        while visible && scenePhase == .active {
          if ["available", "partial", "pending"].contains(paper.text("status")) { await refresh() }
          do { try await Task.sleep(for: .seconds(60)) } catch { return }
        }
      }
      .sheet(item: $presentation) { selection in
        switch selection {
        case .order(let action):
          PaperOrderReviewView(candidate: action.record, initialPolicy: policy.object("risk_policy")) { receipt in
            notice = action.record.text("intent") == "cancel" ? "Cancellation requested" : "Paper order submitted: \(receipt.text("side")) \(receipt.text("qty")) \(receipt.text("symbol"))"
            presentation = nil
            await refresh()
          }
        case .call(let item):
          PaperCallDetailView(call: item.record) { action in
            presentation = .order(TradingRow(record: action, key: "call_id"))
          }
        case .settings:
          TradingPolicyView(policy: policy) { next in await savePolicy(next); return error }
        }
      }
  }
  private var controls: some View {
    HStack(alignment: .center) {
      VStack(alignment: .leading, spacing: 6) {
        Text(policy["entries_paused"] == .bool(true) ? "Entries paused" : policy.text("mode") == "autonomous" ? "Paper autopilot" : "Approve each order").font(.caption.weight(.semibold)).foregroundStyle(Theme.teal)
        Text("\(pending.count) awaiting approval · \(calls.filter { $0.text("status") == "open" }.count) open calls").font(.caption2).foregroundStyle(Theme.secondaryText)
      }
      Spacer()
      Button(policy["entries_paused"] == .bool(true) ? "Resume entries" : "Pause entries") {
        var next = policy
        next["entries_paused"] = .bool(!(policy["entries_paused"] == .bool(true)))
        Task { await savePolicy(next) }
      }.font(.caption.weight(.medium)).disabled(busy)
    }
  }
  @ViewBuilder private var activity: some View {
    ForEach(pending.map { TradingRow(record: $0, key: "candidate_id") }) { item in
      TradingProposalCard(call: item.record, busy: busy, approve: { presentation = .order(item) }, deny: { Task { await deny(item.record) } }, detail: { presentation = .call(TradingRow(record: item.record, key: "call_id")) })
    }
    if !workspace.rows("working_orders").isEmpty {
      TradingSurface {
        Text("Working orders").font(.headline)
        ForEach(workspace.rows("working_orders").map { TradingRow(record: $0, key: "client_order_id") }) { item in
          HStack {
            VStack(alignment: .leading, spacing: 5) {
              Text(item.record.text("symbol")).font(.subheadline.weight(.semibold))
              Text("\(item.record.text("side")) \(item.record.text("qty")) · \(item.record.text("filled_qty")) filled").font(.caption).foregroundStyle(Theme.secondaryText)
              Text(item.record.text("status").replacingOccurrences(of: "_", with: " ")).font(.caption2).foregroundStyle(Theme.secondaryText)
            }
            Spacer()
            Button("Cancel") { presentation = .order(TradingRow(record: ["ticker": .string(item.record.text("symbol")), "intent": .string("cancel"), "order_id": .string(item.record.text("id"))], key: "order_id")) }.font(.caption).disabled(item.record.text("id").isEmpty)
          }
        }
      }
    }
    ForEach((workspace.rows("activity") + earlierEvents).map { TradingRow(record: $0, key: "id") }) { item in
      TradingActivityRow(event: item.record) { openCall(item.record.text("call_id")) }
    }
    if pending.isEmpty && workspace.rows("activity").isEmpty {
      Text("No trading activity yet").font(.subheadline).foregroundStyle(Theme.secondaryText)
    }
    if !cursor.isEmpty { Button("Earlier activity") { Task { await earlier() } }.font(.subheadline).disabled(busy) }
    let expired = workspace.rows("proposals").filter { $0.text("status") == "expired" }
    if !expired.isEmpty {
      DisclosureGroup("Expired calls · \(expired.count)") {
        ForEach(expired.map { TradingRow(record: $0, key: "call_id") }) { item in
          Button { presentation = .call(item) } label: { TradingCallRow(call: item.record) }.buttonStyle(.plain)
        }
      }.font(.caption).tint(Theme.secondaryText)
    }
  }
  private var positions: some View {
    VStack(alignment: .leading, spacing: 20) {
      Text("Open calls").font(.headline)
      ForEach(calls.filter { $0.text("status") == "open" }.map { TradingRow(record: $0, key: "call_id") }) { item in
        Button { presentation = .call(item) } label: { TradingCallRow(call: item.record) }.buttonStyle(.plain)
      }
      if !calls.contains(where: { $0.text("status") == "open" }) { Text("No open calls").font(.subheadline).foregroundStyle(Theme.secondaryText) }
      Text("Account positions").font(.headline)
      ForEach(paper.rows("holdings").map { TradingRow(record: $0, key: "symbol") }) { item in
        TradingSurface {
          HStack { Text("\(item.id) · \(item.record.text("qty")) shares").font(.subheadline.weight(.semibold)); Spacer(); Text(Format.money(item.record.number("market_value"))).font(.subheadline).monospacedDigit() }
          Text("Unrealized \(Format.money(item.record.number("unrealized_pl")))").font(.caption).foregroundStyle(Theme.tone(item.record.number("unrealized_pl")))
          let linked = calls.filter { $0.text("ticker") == item.id && $0.text("status") == "open" }
          if linked.isEmpty {
            Button("Review close") { presentation = .order(TradingRow(record: ["ticker": .string(item.id), "symbol": .string(item.id), "intent": .string("close")], key: "symbol")) }.font(.caption)
          } else {
            DisclosureGroup("Manage calls") {
              ForEach(linked.map { TradingRow(record: $0, key: "call_id") }) { call in
                Button { presentation = .call(call) } label: { TradingCallRow(call: call.record) }.buttonStyle(.plain)
              }
            }.font(.subheadline).tint(Theme.secondaryText)
          }
        }
      }
      if paper.rows("holdings").isEmpty { Text("No open positions").font(.subheadline).foregroundStyle(Theme.secondaryText) }
    }
  }
  private var performance: some View {
    VStack(alignment: .leading, spacing: 20) {
      Text("Agent track record · all time").font(.headline)
      let stats = workspace.object("track_record")
      TradingSurface { Metrics(items: [("Filled calls", stats.text("executed_calls")), ("Closed calls", stats.text("closed_calls")), ("Realized P/L", Format.money(stats.number("realized_pl"))), ("Unrealized P/L", Format.money(stats.number("unrealized_pl"))), ("Win rate · closed calls", Format.percent(stats.number("win_rate_pct"))), ("Profit factor · closed calls", Format.number(stats.number("profit_factor"), digits: 2))])
      if (stats.number("calls_needing_reconciliation") ?? 0) > 0 { Text("\(stats.text("calls_needing_reconciliation")) calls need reconciliation.").font(.caption).foregroundStyle(Theme.coral) } }
      ForEach(calls.map { TradingRow(record: $0, key: "call_id") }) { item in
        Button { presentation = .call(item) } label: { TradingCallRow(call: item.record) }.buttonStyle(.plain)
      }
      if calls.isEmpty { Text("No filled calls yet").font(.subheadline).foregroundStyle(Theme.secondaryText) }
      if let history = resource.value?["tracked_calls"].records, !history.isEmpty {
        DisclosureGroup("Research observations · \(history.count)") {
          ForEach(history.map { TradingRow(record: $0, key: "tracking_id") }) { item in
            NavigationLink { ScrollView { ApprovedCallDetailView(chosen: item.record).padding(20) }.workbench().navigationTitle("Research performance") } label: { HStack { Text(item.record.text("ticker")); Spacer(); Text(Format.percent(item.record.number("return_pct"))) }.font(.subheadline).padding(.vertical, 10) }
          }
        }.font(.subheadline).tint(Theme.secondaryText)
      }
    }
  }
  private func openCall(_ id: String) {
    if let call = (workspace.rows("calls") + workspace.rows("proposals")).first(where: { $0.text("call_id") == id }) { presentation = .call(TradingRow(record: call, key: "call_id")) }
  }
  private func load() async {
    await resource.load { try await session.api.get("/api/trading-agent", query: ["period": period]) }
    earlierEvents = []; cursor = workspace.text("next_cursor")
  }
  private func refresh() async {
    guard !refreshing else { return }
    refreshing = true
    do {
      let _: JSONValue = try await session.api.post("/api/trading-agent/paper/refresh")
      await load(); error = ""
    } catch { self.error = error.localizedDescription }
    refreshing = false
  }
  private func deny(_ call: Record) async {
    busy = true
    do {
      let _: JSONValue = try await session.api.post("/api/trading-agent/decisions", body: ["candidate_id": .string(call.text("candidate_id")), "action": .string("reject")])
      await load(); error = ""
    } catch { self.error = error.localizedDescription }
    busy = false
  }
  private func savePolicy(_ next: Record) async {
    busy = true
    do {
      let _: JSONValue = try await session.api.post("/api/trading-agent/paper/policy", body: ["mode": next["mode"] ?? .string("approval"), "entries_paused": next["entries_paused"] ?? .bool(false), "risk_policy": next["risk_policy"] ?? .object([:]), "expected_revision": next["revision"] ?? .number(0)])
      await load(); error = ""
    } catch { self.error = error.localizedDescription }
    busy = false
  }
  private func earlier() async {
    busy = true
    do {
      let result: JSONValue = try await session.api.get("/api/trading-agent", query: ["period": period, "cursor": cursor])
      let existing = Set((workspace.rows("activity") + earlierEvents).map { $0.text("id") })
      earlierEvents += result["workspace"]["activity"].records.filter { !existing.contains($0.text("id")) }
      cursor = result["workspace"]["next_cursor"].string
    } catch { self.error = error.localizedDescription }
    busy = false
  }
}

private struct TradingProposalCard: View {
  let call: Record
  let busy: Bool
  let approve: () -> Void
  let deny: () -> Void
  let detail: () -> Void
  var body: some View {
    TradingSurface {
      HStack {
        Text(call.text("intent") == "entry" ? "NEW CALL · NEEDS APPROVAL" : "\(call.text("intent").uppercased()) POSITION · NEEDS APPROVAL").font(.system(size: 10, weight: .semibold)).foregroundStyle(Theme.teal)
        Spacer()
        if let date = Format.date(call.text("proposed_at")) { Text(date, format: .dateTime.month(.abbreviated).day()).font(.caption2).foregroundStyle(Theme.secondaryText) }
      }
      HStack { TradingIdentity(symbol: call.text("ticker"), stance: call.text("stance"), font: .title3.weight(.semibold)); Spacer(); Text(call.text("horizon_label").isEmpty ? call.text("horizon_key").uppercased() : call.text("horizon_label")).font(.caption).foregroundStyle(Theme.secondaryText) }
      Text(call.text("reason").isEmpty ? call.text("hypothesis") : call.text("reason")).font(.subheadline).lineSpacing(4)
      if !call.text("quantity").isEmpty { Text("\(call.text("quantity")) shares to \(call.text("intent"))").font(.caption).foregroundStyle(Theme.secondaryText) }
      HStack {
        Button("View thesis", action: detail).font(.caption)
        Spacer()
        Button("Deny", role: .destructive, action: deny).font(.subheadline).padding(.horizontal, 10)
        Button("Approve", action: approve).buttonStyle(TradingPrimaryButton())
      }.disabled(busy)
    }.overlay(alignment: .leading) { RoundedRectangle(cornerRadius: 2).fill(Theme.teal).frame(width: 2).padding(.vertical, 18) }
  }
}
private struct TradingCallRow: View {
  let call: Record
  var body: some View {
    HStack(spacing: 12) {
      VStack(alignment: .leading, spacing: 7) {
        TradingIdentity(symbol: call.text("ticker"), stance: call.text("stance"), font: .subheadline.weight(.semibold))
        Text("\(call.text("status").replacingOccurrences(of: "_", with: " ")) · \(call.text("quantity")) shares").font(.caption2).foregroundStyle(Theme.secondaryText)
      }
      Spacer()
      VStack(alignment: .trailing, spacing: 6) {
        Text(Format.money(call.number("pl"))).font(.subheadline.weight(.medium))
        Text(Format.percent(call.number("return_pct"))).font(.caption)
      }.monospacedDigit().foregroundStyle(Theme.tone(call.number("pl")))
      Image(systemName: "chevron.right").font(.caption2).foregroundStyle(Theme.secondaryText)
    }.padding(.vertical, 12).contentShape(Rectangle())
  }
}
private struct TradingActivityRow: View {
  let event: Record
  let select: () -> Void
  private var payload: Record { event.object("payload") }
  private var title: String {
    switch event.text("kind") {
    case "position_review": "Position review"
    case "call_denied": "Call denied"
    case "submitted": "Order submitted"
    case "cancel_requested": "Cancellation requested"
    case "policy_changed": "Trading settings changed"
    case "action_needs_review": "Action needs review"
    case "monitor_unavailable": "Portfolio review unavailable"
    default: event.text("kind").replacingOccurrences(of: "_", with: " ").capitalized
    }
  }
  var body: some View {
    HStack(alignment: .top, spacing: 14) {
      Circle().fill(Theme.secondaryText).frame(width: 6, height: 6).padding(.top, 6)
      VStack(alignment: .leading, spacing: 10) {
        Text("\(payload.text("ticker").isEmpty ? payload.text("symbol") : payload.text("ticker")) \(title)").font(.subheadline.weight(.medium))
        if let date = Format.date(event.text("occurred_at")) { Text(date, format: .dateTime.month(.abbreviated).day().hour().minute()).font(.caption2).foregroundStyle(Theme.secondaryText) }
        if !payload.text("reason").isEmpty { Text(payload.text("reason")).font(.subheadline).lineSpacing(3) }
        if !payload.text("action").isEmpty { Text(payload.text("action").uppercased()).font(.caption.weight(.medium)).foregroundStyle(Theme.teal) }
        if !payload.text("qty").isEmpty { Text("\(payload.text("side")) \(payload.text("qty")) · \(payload.text("filled_qty")) filled").font(.caption).foregroundStyle(Theme.secondaryText) }
        if !event.text("call_id").isEmpty { Button("View call →", action: select).font(.caption) }
        Divider().padding(.top, 8)
      }.frame(maxWidth: .infinity, alignment: .leading)
    }
  }
}
private struct PaperCallDetailView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.dismiss) private var dismiss
  @StateObject private var resource = Resource<JSONValue>()
  @State private var quantity = ""
  let call: Record
  let manage: (Record) -> Void
  private var candidate: Record { resource.value?["candidate"].dictionaryValue ?? call }
  private var performance: Record { resource.value?["performance"].dictionaryValue ?? call }
  private var path: [Record] { resource.value?["path"].records ?? [] }
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 24) {
          ResourceStatus(resource: resource) { Task { await load() } }
          TradingIdentity(symbol: candidate.text("ticker"), stance: candidate.text("stance"), font: .title2.weight(.semibold))
          TradingSurface { Metrics(items: [("Call P/L", Format.money(performance.number("pl"))), ("Return on filled capital", Format.percent(performance.number("return_pct"))), ("Realized", Format.money(performance.number("realized_pl"))), ("Unrealized", Format.money(performance.number("unrealized_pl")))]) }
          if performance.number("entry_capital") != nil {
            TradingSurface { Metrics(items: [("Average entry", Format.price(performance.number("average_entry"))), ("Filled capital", Format.money(performance.number("entry_capital"))), ("Remaining shares", performance.text("quantity")), ("Current mark", Format.price(performance.number("mark_price")))]) }
            Text("Fills and linked fees").font(.caption2).foregroundStyle(Theme.secondaryText)
          }
          if !path.isEmpty {
            Chart {
              ForEach(path.map { TradingRow(record: $0, key: "date") }) { item in
                if let date = Format.date(item.id), let value = item.record.number("pl") {
                  LineMark(x: .value("Date", date), y: .value("P/L", value)).foregroundStyle(Theme.teal)
                  PointMark(x: .value("Date", date), y: .value("P/L", value)).foregroundStyle(Theme.teal)
                }
              }
            }.frame(height: 180).accessibilityLabel("Call profit and loss in US dollars")
          }
          TradingNarrative(title: "Thesis", text: candidate.text("hypothesis"))
          TradingNarrative(title: "Recommendation", text: candidate.text("reason"))
          TradingNarrative(title: "Invalidation", text: candidate.text("invalidation"))
          TradingNarrative(title: "At the call price", text: candidate.object("forecast").text("pricing_assessment"))
          TradingNarrative(title: "Opposing case", text: candidate.object("forecast").text("opposing_case"))
          ForEach([("evidence", "Evidence"), ("tail_risks", "Risks"), ("evidence_limits", "Research next")], id: \.0) { key, label in
            let items = candidate[key]?.array ?? []
            if !items.isEmpty { DisclosureGroup(label) { ForEach(Array(items.enumerated()), id: \.offset) { _, value in MarkdownView(text: value.string).padding(.vertical, 6) } }.font(.subheadline).tint(Theme.secondaryText) }
          }
          if performance.text("status") == "needs_reconciliation" { Failure(message: "Fill allocation needs reconciliation. Refresh the account.") }
          if performance.text("status") == "open" { management }
          if !performance.rows("fills").isEmpty {
            DisclosureGroup("Fills · \(performance.rows("fills").count)") {
              ForEach(performance.rows("fills").map { TradingRow(record: $0, key: "id") }) { item in
                VStack(alignment: .leading, spacing: 5) {
                  Text("\(item.record.text("side")) \(item.record.text("qty")) at \(Format.money(item.record.number("price")))").font(.subheadline)
                  if let date = Format.date(item.record.text("transaction_time")) { Text(date, format: .dateTime.month(.abbreviated).day().hour().minute()).font(.caption2).foregroundStyle(Theme.secondaryText) }
                }.padding(.vertical, 8)
              }
            }.font(.subheadline).tint(Theme.secondaryText)
          }
          NavigationLink("Company details") { InvestigatorView(ticker: candidate.text("ticker")) }.font(.subheadline)
          ForEach((resource.value?["activity"].records ?? []).map { TradingRow(record: $0, key: "id") }) { item in TradingActivityRow(event: item.record) {} }
        }.padding(20)
      }.workbench().navigationTitle("Call performance").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        .task { await load() }
    }.preferredColorScheme(.dark).tint(Theme.teal)
  }
  private var management: some View {
    TradingSurface {
      Text("\(performance.text("quantity")) shares remaining").font(.subheadline.weight(.medium))
      TextField("Shares to reduce", text: $quantity).keyboardType(.decimalPad).font(.subheadline)
      HStack {
        Button("Review reduction") { action("reduce", quantity: quantity) }.buttonStyle(.bordered).disabled((Double(quantity) ?? 0) <= 0 || (Double(quantity) ?? 0) >= (Double(performance.text("quantity")) ?? 0))
        Button("Review close") { action("close") }.buttonStyle(TradingPrimaryButton())
      }
    }
  }
  private func action(_ intent: String, quantity: String? = nil) {
    var next: Record = ["ticker": .string(call.text("ticker")), "intent": .string(intent), "call_id": .string(call.text("call_id"))]
    if let quantity { next["quantity"] = .string(quantity) }
    manage(next)
  }
  private func load() async { await resource.load { try await session.api.get("/api/trading-agent/paper/calls/\(call.text("call_id"))") } }
}
private struct TradingPolicyView: View {
  @Environment(\.dismiss) private var dismiss
  @State private var mode: String
  @State private var paused: Bool
  @State private var risk: Record
  @State private var busy = false
  @State private var error = ""
  let policy: Record
  let save: (Record) async -> String
  init(policy: Record, save: @escaping (Record) async -> String) {
    self.policy = policy; self.save = save
    _mode = State(initialValue: policy.text("mode")); _paused = State(initialValue: policy["entries_paused"] == .bool(true)); _risk = State(initialValue: policy.object("risk_policy"))
  }
  var body: some View {
    NavigationStack {
      Form {
        Section("Execution") {
          Picker("Mode", selection: $mode) { Text("Approve each order").tag("approval"); Text("Paper autopilot").tag("autonomous") }
          if mode == "autonomous" { Text("The agent can place new paper entries and position changes within these limits on each Trading run. Existing proposals still need your approval.").font(.caption) }
          Toggle("Pause new entries", isOn: $paused)
        }
        Section("Annualized risk limits") {
          ForEach([("position_volatility_pct", "Position volatility budget (%)"), ("portfolio_volatility_pct", "Portfolio volatility limit (%)"), ("max_position_pct", "Maximum position (% equity)"), ("max_gross_pct", "Maximum gross exposure (%)")], id: \.0) { key, label in
            HStack { Text(label).font(.caption); TextField(label, value: Binding(get: { risk.number(key) ?? 0 }, set: { risk[key] = .number($0) }), format: .number).keyboardType(.decimalPad).multilineTextAlignment(.trailing).frame(width: 65) }
          }
        }
        if !error.isEmpty { Failure(message: error) }
        Button(mode == "autonomous" && policy.text("mode") != "autonomous" ? "Enable paper autopilot" : "Save settings") {
          busy = true
          Task {
            error = await save(["mode": .string(mode), "entries_paused": .bool(paused), "risk_policy": .object(risk), "revision": policy["revision"] ?? .number(0)])
            busy = false
            if error.isEmpty { dismiss() }
          }
        }.disabled(busy)
      }.navigationTitle("Trading settings").navigationBarTitleDisplayMode(.inline).toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close") { dismiss() }.disabled(busy) } }
    }.preferredColorScheme(.dark).tint(Theme.teal)
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
        Text("ROI").font(.caption).foregroundStyle(Theme.secondaryText)
        Text(Format.percent(current.number("return_pct"))).font(.system(.largeTitle, design: .rounded).weight(.semibold)).foregroundStyle(Theme.tone(current.number("return_pct"))).monospacedDigit()
      }
      ResourceStatus(resource: detail) { Task { await load() } }
      if !current.rows("path").isEmpty { PaperReturnChart(points: current.rows("path"), benchmark: true, seriesLabel: "Call", benchmarkLabel: benchmark) }
      TradingSurface {
        LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)], alignment: .leading, spacing: 22) {
          TradingMetric(label: benchmark, value: Format.percent(current.number("benchmark_return_pct")))
          TradingMetric(label: "Difference", value: Format.percent(current.number("excess_return_pct")), tone: Theme.tone(current.number("excess_return_pct")))
          TradingMetric(label: "Drawdown", value: Format.percent(current.number("max_drawdown_pct")))
          TradingMetric(label: "Sharpe", value: Format.number(current.object("performance").number("sharpe_ratio"), digits: 2))
        }
        if !current.object("performance").isEmpty { PaperPerformanceBasis(performance: current.object("performance")) }
      }
      if ["pending_entry", "price_unavailable"].contains(current.text("status")) || current.text("path_status") == "unavailable" {
        Text(current.text("status") == "pending_entry" ? "Entry at the next trading day's close." : "Price history is unavailable.").font(.caption).foregroundStyle(Theme.secondaryText)
      }
      VStack(spacing: 12) {
        datedRow(current.text("entry_date").isEmpty ? "Scheduled entry" : "Entry", raw: current.text("entry_date").isEmpty ? current.text("scheduled_entry_date") : current.text("entry_date"))
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

private struct PaperPerformanceBasis: View {
  let performance: Record
  var body: some View {
    Text("\(performance.text("daily_observations")) daily \(performance.number("daily_observations") == 1 ? "return" : "returns") · Sharpe annualized · \(Format.number(performance.number("risk_free_rate_pct"), digits: 0))% risk-free")
      .font(.caption2).foregroundStyle(Theme.secondaryText)
    if performance.number("sharpe_ratio") == nil {
      Text((performance.number("daily_observations") ?? 0) < 2 ? "Sharpe needs at least 2 daily returns." : "Sharpe is undefined at zero volatility.")
        .font(.caption2).foregroundStyle(Theme.secondaryText)
    }
  }
}

private struct PaperPortfolioView: View {
  let portfolio: Record
  @Binding var period: String
  let refreshing: Bool
  let refresh: () -> Void
  private var summary: Record { portfolio.object("summary") }
  private var available: Bool { ["available", "partial"].contains(portfolio.text("status")) }
  private var dataPeriod: String { portfolio.text("period").isEmpty ? period : portfolio.text("period") }
  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      HStack {
        Label("Alpaca · Paper portfolio", systemImage: "building.columns").font(.caption).foregroundStyle(Theme.secondaryText)
        Spacer()
        Button(action: refresh) { if refreshing { ProgressView().controlSize(.small) } else { Image(systemName: "arrow.clockwise") } }.disabled(refreshing || portfolio.text("status") == "unlinked").accessibilityLabel("Refresh paper portfolio")
      }
      if available {
        Text(Format.money(summary.number("equity"))).font(.system(size: 38, weight: .semibold, design: .rounded)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
        HStack(spacing: 6) {
          Text(Format.money(portfolio.number("profit_loss")))
          Text("(\(Format.percent(portfolio.number("return_pct"))))")
          Text(dataPeriod == "ALL" ? "all time" : dataPeriod).foregroundStyle(Theme.secondaryText)
        }.font(.subheadline).monospacedDigit().foregroundStyle(Theme.tone(portfolio.number("profit_loss")))
        PaperEquityHistory(points: portfolio.rows("path"), window: $period, dataPeriod: dataPeriod)
        TradingSurface {
          Metrics(items: [("Cash", Format.money(summary.number("cash"))), ("Open P/L", Format.money(summary.number("unrealized_pl"))), ("\(dataPeriod) drawdown", Format.percent(portfolio.number("max_drawdown_pct"))), ("\(dataPeriod) Sharpe", Format.number(portfolio.object("performance").number("sharpe_ratio"), digits: 2))])
        }
        if portfolio.text("status") == "partial" { Label("Refresh incomplete", systemImage: "clock").font(.caption).foregroundStyle(Theme.coral) }
        if let date = Format.date(portfolio.text("as_of")) { Text("Updated \(date.formatted(date: .abbreviated, time: .shortened)) · cashflow adjusted").font(.caption2).foregroundStyle(Theme.secondaryText) }
      } else { Text(portfolio.text("status") == "unlinked" ? "No paper account connected" : "Paper account unavailable").font(.subheadline).foregroundStyle(Theme.secondaryText) }
    }
  }
}

private struct PaperEquityHistory: View {
  let points: [Record]
  @Binding var window: String
  let dataPeriod: String
  @State private var selectedDate: Date?
  private var filtered: [Record] {
    points
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
        Text(dataPeriod).foregroundStyle(Theme.secondaryText)
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
        .accessibilityLabel("Alpaca account equity in US dollars, \(dataPeriod) history")
      HStack(spacing: 8) {
        ForEach(["1D", "1W", "1M", "3M", "1Y", "ALL"], id: \.self) { value in
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
  let initialPolicy: Record
  let submitted: (Record) async -> Void
  @State private var review: Record = [:]
  @State private var busy = false
  @State private var error = ""
  private var intent: String { candidate.text("intent").isEmpty ? "entry" : candidate.text("intent") }
  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 20) {
          Text("Alpaca · Paper").font(.caption).foregroundStyle(Theme.secondaryText)
          if !error.isEmpty { Failure(message: error) }
          if review.isEmpty && busy { ProgressView("Preparing order review…") }
          if !review.isEmpty {
            let order = review.object("order"), risk = review.object("risk")
            TradingSurface {
              Text(intent == "cancel" ? "Request cancellation" : "\(order.text("side") == "buy" ? "Buy" : intent == "entry" ? "Short" : "Sell") \(order.text("qty")) shares").font(.title2.weight(.semibold))
              Text("\(order.text("symbol")) · \(order.text("type")) · \(order.text("time_in_force"))").font(.subheadline).foregroundStyle(Theme.secondaryText)
              if intent == "cancel" {
                Text("\(order.text("filled_qty")) shares already filled · \(order.text("status"))").font(.caption).foregroundStyle(Theme.secondaryText)
              } else {
                Metrics(items: [("Estimated amount", Format.money(risk.number("suggested_notional"))), ("Volatility before", Format.percent(risk.number("portfolio_volatility_before_pct"))), ("Volatility after", Format.percent(risk.number("portfolio_volatility_after_pct")))] + (risk.number("observations") == nil ? [] : [("Daily observations", risk.text("observations"))]))
              }
              if !risk.text("risk_error").isEmpty { Text(risk.text("risk_error")).font(.caption).foregroundStyle(Theme.coral) }
              if let expires = Format.date(review.text("expires_at")) { Text("Review expires \(expires.formatted(date: .omitted, time: .shortened))").font(.caption).foregroundStyle(Theme.secondaryText) }
              Button(busy ? "Submitting…" : intent == "cancel" ? "Request cancellation" : "Confirm paper order") { Task { await confirm() } }.buttonStyle(TradingPrimaryButton()).disabled(busy)
              Button("Refresh review") { Task { await calculate() } }.font(.subheadline).disabled(busy)
            }
          } else if !busy { Button("Retry review") { Task { await calculate() } }.buttonStyle(TradingPrimaryButton()) }
          if intent == "entry" {
            DisclosureGroup("Sizing limits") {
              Text("Ledoit–Wolf covariance · annualized volatility").font(.caption).foregroundStyle(Theme.secondaryText)
              Metrics(items: [("Position volatility budget", Format.percent(initialPolicy.number("position_volatility_pct"))), ("Portfolio volatility limit", Format.percent(initialPolicy.number("portfolio_volatility_pct"))), ("Maximum position", Format.percent(initialPolicy.number("max_position_pct"))), ("Maximum gross exposure", Format.percent(initialPolicy.number("max_gross_pct")))])
            }.font(.subheadline).tint(Theme.secondaryText)
          }
        }.padding(20)
      }.workbench().navigationTitle("\(candidate.text("ticker")) · \(intent == "entry" ? "Approve call" : intent.capitalized)").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close") { dismiss() }.disabled(busy) } }
        .task { await calculate() }
    }.preferredColorScheme(.dark).tint(Theme.teal)
  }
  private func calculate() async {
    busy = true; error = ""; review = [:]
    do {
      let keys = ["candidate_id", "intent", "call_id", "quantity", "order_id", "symbol"]
      let body = candidate.filter { keys.contains($0.key) }
      let value: JSONValue = try await session.api.post("/api/trading-agent/paper/review", body: body)
      guard let ticket = value.dictionaryValue else { throw APIError.invalidResponse }
      review = ticket
    } catch { self.error = error.localizedDescription }
    busy = false
  }
  private func confirm() async {
    busy = true; error = ""
    do {
      let result: JSONValue = try await session.api.post("/api/trading-agent/paper/confirm", body: ["ticket_id": .string(review.text("ticket_id")), "arguments_hash": .string(review.text("arguments_hash"))])
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
  private var axisDates: [Date] {
    let dates = Array(Set(points.compactMap { Format.date($0.text("date")) })).sorted()
    guard dates.count > 3 else { return dates }
    return [dates[0], dates[(dates.count - 1) / 2], dates[dates.count - 1]]
  }
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
        .chartXScale(range: .plotDimension(padding: 16))
        .chartXAxis { AxisMarks(values: axisDates) { value in
          AxisValueLabel { if let date = value.as(Date.self) { Text(Format.historyDate(date, years: false)).font(.caption2).foregroundStyle(Theme.secondaryText).fixedSize() } }
        } }
        .chartYAxis { AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
          AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3])).foregroundStyle(Theme.border)
          AxisValueLabel { if let number = value.as(Double.self) { Text(Format.percent(number)).font(.caption2).foregroundStyle(Theme.secondaryText) } }
        } }.frame(height: 200).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
        .accessibilityLabel("\(seriesLabel) and \(benchmarkLabel) return history in percent")
    }
  }
}
