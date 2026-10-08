import Charts
import SwiftUI

struct MarketsView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var catalog = Resource<MarketCatalog>()
  @StateObject private var data = Resource<[Record]>()
  @State private var mode = "business"
  @State private var lensName = ""
  @State private var horizon = "1m"
  @State private var search = ""
  private var lenses: [MarketCatalog.Lens] {
    (mode == "commodities" ? catalog.value?.commodities : catalog.value?.business) ?? []
  }
  private var selectedLens: MarketCatalog.Lens? {
    lenses.first { $0.name == lensName } ?? lenses.first
  }
  private var selectedHorizon: MarketCatalog.Horizon? {
    catalog.value?.horizons.first { $0.key == horizon } ?? catalog.value?.horizons.first
  }
  private var rows: [Record] {
    (data.value ?? []).filter {
      search.isEmpty || $0.text("symbol").localizedCaseInsensitiveContains(search)
        || $0.text("company_name").localizedCaseInsensitiveContains(search)
    }
    .sorted {
      ($0.number(selectedHorizon?.column ?? "") ?? -.infinity)
        > ($1.number(selectedHorizon?.column ?? "") ?? -.infinity)
    }
  }
  private var selectionKey: String { "\(mode)|\(selectedLens?.name ?? "")|\(horizon)" }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        Picker("Market", selection: $mode) {
          Text("Businesses").tag("business")
          Text("Broad").tag("broad")
          Text("Commodities").tag("commodities")
        }.pickerStyle(.segmented)
        HStack {
          Picker(
            "Lens", selection: Binding(get: { selectedLens?.name ?? "" }, set: { lensName = $0 })
          ) { ForEach(lenses) { Text($0.name).tag($0.name) } }.frame(
            maxWidth: .infinity, alignment: .leading)
          Picker("Horizon", selection: $horizon) {
            ForEach(catalog.value?.horizons ?? []) { Text($0.label).tag($0.key) }
          }
        }
        ResourceStatus(resource: catalog) { Task { await loadCatalog() } }
        ResourceStatus(resource: data) { Task { await load() } }
        if !rows.isEmpty {
          let returns = rows.compactMap { $0.number(selectedHorizon?.column ?? "") }
          Metrics(items: [
            ("Universe", String(rows.count)),
            ("Advancing", "\(returns.filter { $0 > 0 }.count)/\(rows.count)"),
            (
              "Average return",
              Format.percent(returns.isEmpty ? nil : returns.reduce(0, +) / Double(returns.count))
            ), ("Leader", rows.first?.text("symbol") ?? "—"),
          ])
          if mode == "business" {
            Panel("Volume and daily move") {
              TreemapChart(
                tiles: rows.enumerated().map { index, row in
                  .init(
                    id: index, label: row.text("symbol"), group: "",
                    weight: row.number("volume") ?? 0, color: row.number("daily_change_pct"))
                }, weightLabel: "Volume", colorLabel: "1D %")
            }
            Panel("Momentum structure") {
              ScatterPlot(
                rows: rows, x: "momentum_score", y: selectedHorizon?.column ?? "return_1m_pct",
                label: "symbol", xTitle: "Momentum",
                yTitle: "\(selectedHorizon?.label ?? "Return") %", color: "opportunity_score",
                size: "volume")
            }
          } else {
            Panel("Return topology") {
              SpatialPlot(
                points: rows.spatial(
                  x: "return_1m_pct", y: "return_3m_pct", z: "return_1y_pct", label: "symbol",
                  color: "momentum_roc_score"), axes: ["1M %", "3M %", "1Y %"],
                colorLabel: "Momentum rate of change")
            }
            Panel("Multi-horizon returns") {
              MatrixChart(
                columns: catalog.value?.horizons.map(\.label) ?? [],
                rows: rows.map { $0.text("symbol") },
                values: rows.map { row in
                  (catalog.value?.horizons ?? []).map { row.number($0.column) }
                })
            }
          }
          HStack {
            Text(selectedLens?.name ?? "Markets").font(.headline)
            Spacer()
            Text("\(rows.count) symbols").font(.caption).foregroundStyle(.secondary)
          }
          ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
            NavigationLink {
              InvestigatorView(ticker: row.text("symbol"))
            } label: {
              HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                  Text(row.text("symbol")).font(.headline)
                  Text(row.text("company_name")).font(.caption).foregroundStyle(.secondary)
                    .lineLimit(2)
                  Text(row.text("direction")).font(.caption2).foregroundStyle(.secondary)
                  Text(
                    "1D \(Format.percent(row.number("daily_change_pct"))) · 3M \(Format.percent(row.number("return_3m_pct"))) · Score \(Format.number(row.number("opportunity_score")))"
                  ).font(.caption2).foregroundStyle(.secondary)
                }
                Spacer(minLength: 12)
                VStack(alignment: .trailing, spacing: 5) {
                  Text(Format.money(row.number("close"))).monospacedDigit()
                  Text(Format.percent(row.number(selectedHorizon?.column ?? ""))).foregroundStyle(
                    Theme.tone(row.number(selectedHorizon?.column ?? ""))
                  ).monospacedDigit()
                }.font(.subheadline)
              }.padding(.vertical, 6).contentShape(Rectangle())
            }.buttonStyle(.plain)
            Divider()
          }
        } else if !data.loading, data.value != nil {
          Text("No matching symbols").foregroundStyle(.secondary)
        }
      }.padding(20)
    }
    .navigationTitle("Markets").navigationBarTitleDisplayMode(.large)
    .searchable(text: $search, prompt: "Symbol or company")
    .workbench()
    .researchContext(
      "Market Explorer; lens=\(selectedLens?.name ?? ""); horizon=\(selectedHorizon?.label ?? "")"
    )
    .task { await loadCatalog() }
    .task(id: selectionKey) { await load() }
    .refreshable { await load() }
    .onChange(of: mode) { _, _ in lensName = lenses.first?.name ?? "" }
  }
  private func loadCatalog() async {
    await catalog.load { try await session.api.get("/api/market-lenses") }
    if lensName.isEmpty { lensName = lenses.first?.name ?? "" }
  }
  private func load() async {
    guard let lens = selectedLens, let horizon = selectedHorizon else { return }
    let key = selectionKey
    await data.load(key: key) {
      if mode == "business" {
        return try await session.api.dataset(
          "market_opportunity_feed",
          params: [
            "business_filter": .string(lens.name), "selected_horizon_col": .string(horizon.column),
            "selected_horizon_label": .string(horizon.label),
            "symbols": .array(lens.symbols.map(JSONValue.string)), "limit": .number(80),
          ])
      }
      return try await session.api.dataset(
        "momentum_profiles",
        params: ["days": .number(3650), "symbols": .array(lens.symbols.map(JSONValue.string))])
    }
  }
}
