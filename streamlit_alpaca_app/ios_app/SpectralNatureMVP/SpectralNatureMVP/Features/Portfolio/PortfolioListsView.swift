import SwiftUI

struct PortfolioListsView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<PortfolioOpportunitySnapshot>()
  let period: String
  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      ResourceStatus(resource: resource) { Task { await load() } }
      if let data = resource.value {
        if data.status == "available" {
          PortfolioListsAnalysis(data: data)
        } else {
          Text("List histories unavailable").foregroundStyle(.secondary)
        }
      }
    }.task(id: period) { await load() }
  }
  private func load() async {
    await resource.load(key: period, clearOnFailure: true) {
      try await session.api.get("/api/portfolio/opportunities", query: ["period": period])
    }
  }
}

struct PortfolioListsAnalysis: View {
  let data: PortfolioOpportunitySnapshot
  @State private var selectedGroups: Set<String> = []
  @State private var showingGroups = false
  @State private var lens = "Return drivers"
  @State private var depth = 2
  @State private var inspectedGroup = ""
  init(data: PortfolioOpportunitySnapshot) {
    self.data = data
    _selectedGroups = State(
      initialValue: Set(
        (Array(data.rankings.prefix(3)) + Array(data.rankings.suffix(3))).map {
          $0.text("group_key")
        }))
  }
  private var selected: [Record] {
    data.rankings.filter { selectedGroups.contains($0.text("group_key")) }
  }
  private var history: [Record] {
    var rows: [String: Record] = [:]
    for series in data.series where selectedGroups.contains(series.groupKey) {
      for point in series.points {
        rows[point.date, default: ["timestamp": .string(point.date)]][series.groupKey] = .number(
          point.value)
      }
    }
    return rows.keys.sorted().compactMap { rows[$0] }
  }
  private var flow: (nodes: [SankeyChart.Node], links: [SankeyChart.Link]) {
    let contract = [
      "Return drivers": ("return_contribution", "direction"),
      "Risk concentration": ("risk_contribution", "risk_direction"),
      "Momentum regimes": ("regime_intensity", "regime"),
    ][lens]!
    let ranked = data.rankings.filter { $0.number("return") != nil }
    let best = Array(ranked.prefix(depth))
    let bestKeys = Set(best.map { $0.text("group_key") })
    let worst = Array(
      ranked.filter { !bestKeys.contains($0.text("group_key")) }.suffix(depth).reversed())
    var nodes: [SankeyChart.Node] = []
    var links: [SankeyChart.Link] = []
    for group in best + worst {
      let key = group.text("group_key")
      let name = group.text("group")
      let rows = data.contributions.filter {
        $0.text("group_key") == key && ($0.number(contract.0) ?? 0) != 0
      }
      guard !rows.isEmpty else { continue }
      let groupId = "group:\(key)"
      nodes.append(
        .init(
          id: groupId,
          label: "\(bestKeys.contains(key) ? "Best" : "Worst") · \(name)",
          stage: 0, tone: group.number("return") ?? 0,
          detail: Format.percent(group.number("return"), fraction: true), labelLines: 2))
      let categories = Dictionary(grouping: rows, by: { $0.text(contract.1) })
      for category in categories.keys.sorted() {
        let members = categories[category]!.sorted {
          abs($0.number(contract.0) ?? 0) > abs($1.number(contract.0) ?? 0)
        }
        let categoryId = "category:\(key):\(category)"
        let signed = members.reduce(0) { $0 + ($1.number(contract.0) ?? 0) }
        let magnitude = members.reduce(0) { $0 + abs($1.number(contract.0) ?? 0) }
        nodes.append(.init(id: categoryId, label: category, stage: 1, tone: signed, labelLines: 2))
        links.append(
          .init(
            id: links.count, source: groupId, target: categoryId, magnitude: magnitude,
            signed: signed, label: "\(name) · \(category)"))
        var leaves = members.prefix(3).map {
          ($0.text("symbol"), $0.number(contract.0) ?? 0, abs($0.number(contract.0) ?? 0))
        }
        if members.count > 3 {
          let remaining = members.dropFirst(3)
          leaves.append(
            (
              "Other (\(remaining.count))",
              remaining.reduce(0) { $0 + ($1.number(contract.0) ?? 0) },
              remaining.reduce(0) { $0 + abs($1.number(contract.0) ?? 0) }
            ))
        }
        for (symbol, value, magnitude) in leaves {
          let leafId = "\(categoryId):\(symbol)"
          nodes.append(.init(id: leafId, label: symbol, stage: 2, tone: value))
          links.append(
            .init(
              id: links.count, source: categoryId, target: leafId, magnitude: magnitude,
              signed: value, label: "\(name) · \(category) · \(symbol)"))
        }
      }
    }
    return (nodes, links)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      Panel("Equal-weight list performance · \(data.returnPeriod)") {
        Button("Compare groups · \(selectedGroups.count)") { showingGroups = true }.buttonStyle(
          .bordered)
        if !selected.isEmpty {
          TimeSeriesChart(
            rows: history, series: selected.map { ($0.text("group_key"), $0.text("group")) },
            height: 270)
        }
      }
      Panel("Group risk and return") {
        ScatterPlot(
          rows: selected, x: "volatility", y: "return", label: "group",
          xTitle: "\(data.riskPeriod) annualized volatility %",
          yTitle: "\(data.returnPeriod) return %",
          color: "breadth", size: "members", scale: 100, colorDomain: 0...1, colorAsFraction: true,
          colorTitle: "Positive breadth", percentAxes: true, identity: "group_key")
        Picker("Inspect group", selection: $inspectedGroup) {
          Text("Select group").tag("")
          ForEach(selected.indices, id: \.self) { index in
            let row = selected[index]
            Text(row.text("group")).tag(row.text("group_key"))
          }
        }
        if let group = selected.first(where: { $0.text("group_key") == inspectedGroup }) {
          Metrics(items: [
            ("Return", Format.percent(group.number("return"), fraction: true)),
            ("Positive breadth", Format.percent(group.number("breadth"), fraction: true)),
            ("Max drawdown", Format.percent(group.number("max_drawdown"), fraction: true)),
            ("Daily tail loss", Format.percent(group.number("tail_loss_5pct"), fraction: true)),
            ("Independent risk bets", Format.number(group.number("effective_risk_bets"))),
            ("Diversification", Format.number(group.number("diversification_ratio"))),
            (
              "Top-three contribution",
              Format.percent(group.number("top_three_concentration"), fraction: true)
            ),
            ("Regime", group.text("regime")),
          ])
        }
      }
      Panel("Best/worst by \(data.returnPeriod) return") {
        Picker("Flow lens", selection: $lens) {
          ForEach(["Return drivers", "Risk concentration", "Momentum regimes"], id: \.self) {
            Text($0)
          }
        }
        Stepper(
          "Groups per side · \(depth)", value: $depth,
          in: 1...min(5, max(1, data.rankings.count / 2)))
        Text(
          lens == "Return drivers"
            ? "\(data.returnPeriod) return contribution"
            : lens == "Risk concentration"
              ? "\(data.riskPeriod) annualized volatility contribution"
              : "\(data.riskPeriod) momentum intensity"
        )
        .font(.caption2).foregroundStyle(.secondary)
        let graph = flow
        if !graph.links.isEmpty {
          SankeyChart(nodes: graph.nodes, links: graph.links, fraction: lens != "Momentum regimes")
            .id("\(lens)-\(depth)")
        } else {
          Text("Contribution history unavailable").foregroundStyle(.secondary)
        }
      }
      let indices = data.correlation.labels.indices.filter { index in
        data.correlation.keys.map { selectedGroups.contains($0[index]) }
          ?? selected.contains { $0.text("group") == data.correlation.labels[index] }
      }
      if !indices.isEmpty {
        Panel("Group-return correlation · \(data.riskPeriod)") {
          CorrelationGrid(
            labels: indices.map { data.correlation.labels[$0] },
            values: indices.map { i in indices.map { j in data.correlation.values[i][j] } })
        }
      }
      DisclosureGroup("All list diagnostics") {
        WorkbookTable(
          rows: data.rankings,
          columns: [
            .init(key: "group", title: "Group"),
            .init(key: "return", title: "Return", style: .fraction),
            .init(key: "history_coverage", title: "History coverage", style: .fraction),
            .init(key: "start_at", title: "History start"),
            .init(key: "end_at", title: "History end"),
            .init(key: "volatility", title: "Volatility", style: .fraction),
            .init(key: "breadth", title: "Breadth", style: .fraction),
            .init(key: "max_drawdown", title: "Drawdown", style: .fraction),
            .init(key: "effective_risk_bets", title: "Risk bets", style: .number),
            .init(key: "regime", title: "Regime"),
            .init(key: "members", title: "Members", style: .number),
          ])
      }
      DisclosureGroup("Lists and screeners") {
        WorkbookTable(
          rows: data.sets,
          columns: [
            .init(key: "name", title: "Group"), .init(key: "kind", title: "Type"),
            .init(key: "member_count", title: "Members", style: .number),
            .init(key: "analytics_status", title: "Analytics"),
          ])
      }
    }
    .sheet(isPresented: $showingGroups) {
      NavigationStack {
        List(Array(data.rankings.enumerated()), id: \.offset) { _, row in
          Toggle(
            isOn: Binding(
              get: { selectedGroups.contains(row.text("group_key")) },
              set: { value in
                if value {
                  selectedGroups.insert(row.text("group_key"))
                } else {
                  selectedGroups.remove(row.text("group_key"))
                }
              })
          ) {
            Text(row.text("group"))
            Text(Format.percent(row.number("return"), fraction: true)).font(.caption)
          }
          .disabled(!selectedGroups.contains(row.text("group_key")) && selectedGroups.count >= 6)
        }.navigationTitle("Compare up to six groups")
          .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { showingGroups = false } }
          }
      }
    }
  }
}
