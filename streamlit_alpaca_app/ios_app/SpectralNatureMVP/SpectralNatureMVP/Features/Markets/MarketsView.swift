import Charts
import SwiftUI

struct MarketsView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var catalog = Resource<MarketCatalog>()
  @StateObject private var data = Resource<[Record]>()
  @StateObject private var candidates = Resource<[Record]>()
  @State private var mode = "business"
  @State private var lensName = ""
  @State private var horizon = "1m"
  @State private var search = ""
  @State private var showingVolume = false
  @State private var showingSpatial = false
  @State private var showingMatrix = false
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
    // Complete retained return histories own the plots. The ranked research feed
    // adds names/volume/context but must not truncate the numerical universe.
    let metadata = Dictionary(grouping: candidates.value ?? [], by: { $0.text("symbol") })
    let symbols = Set(selectedLens?.symbols ?? [])
    return (data.value ?? []).filter { symbols.contains($0.text("symbol")) }.map { profile in
      (metadata[profile.text("symbol")]?.first ?? [:]).merging(profile) { _, value in value }
    }.filter {
      search.isEmpty || $0.text("symbol").localizedCaseInsensitiveContains(search)
        || $0.text("company_name").localizedCaseInsensitiveContains(search)
    }.sorted {
      let a = $0.number(selectedHorizon?.column ?? "") ?? -.infinity
      let b = $1.number(selectedHorizon?.column ?? "") ?? -.infinity
      return a == b ? $0.text("symbol") < $1.text("symbol") : a > b
    }
  }
  private var selectionKey: String { "\(mode)|\(selectedLens?.name ?? "")" }
  private var candidateKey: String { "\(selectionKey)|\(horizon)" }
  var body: some View {
    // Join and rank the universe once per update, not once per consumer below.
    let rows = self.rows
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        Picker("Market", selection: $mode) {
          Text("Businesses").tag("business")
          Text("Broad").tag("broad")
          Text("Commodities").tag("commodities")
        }.pickerStyle(.segmented)
        HStack {
          Picker("Lens", selection: Binding(get: { selectedLens?.name ?? "" }, set: { lensName = $0 })) {
            ForEach(lenses) { Text($0.name).tag($0.name) }
          }.frame(maxWidth: .infinity, alignment: .leading)
          Picker("Return period", selection: $horizon) {
            ForEach(catalog.value?.horizons ?? []) { Text($0.label).tag($0.key) }
          }
        }
        ResourceStatus(resource: catalog) { Task { await loadCatalog() } }
        ResourceStatus(resource: data) { Task { await load() } }
        if !rows.isEmpty, let selectedHorizon {
          let returns = rows.compactMap { $0.number(selectedHorizon.column) }
          Metrics(items: [
            ("Universe", String(rows.count)),
            ("Advancing", returns.isEmpty ? "—" : "\(returns.filter { $0 > 0 }.count)/\(returns.count)"),
            ("Average return", Format.percent(returns.isEmpty ? nil : returns.reduce(0, +) / Double(returns.count))),
            ("Leader", returns.isEmpty ? "—" : rows.first?.text("symbol") ?? "—"),
          ])
          MarketReturnAnalysis(rows: rows, horizons: catalog.value?.horizons ?? [], horizon: selectedHorizon)
            .id(selectionKey)
          if mode == "business" {
            Panel("Trading volume") {
              DisclosureGroup("Shares traded and daily move", isExpanded: $showingVolume) {
                if showingVolume {
                  ResourceStatus(resource: candidates) { Task { await loadCandidates() } }
                  let volumeRows = rows.filter { ($0.number("volume") ?? 0) > 0 }
                  Text("\(volumeRows.count)/\(rows.count) instruments with volume")
                    .font(.caption2).foregroundStyle(.secondary)
                  TreemapChart(
                    tiles: volumeRows.enumerated().map { index, row in
                      .init(id: index, label: row.text("symbol"), group: "",
                        weight: row.number("volume") ?? 0, color: row.number("daily_change_pct"))
                    }, weightLabel: "Shares traded", colorLabel: "1D %")
                }
              }.font(.subheadline)
            }
          } else {
            Panel("More comparisons") {
              DisclosureGroup("3D returns", isExpanded: $showingSpatial) {
                if showingSpatial {
                  Text("One point per instrument · 1 Month / 3 Month / 1 Year returns")
                    .font(.caption2).foregroundStyle(.secondary)
                  SpatialPlot(
                    points: rows.spatial(x: "return_1m_pct", y: "return_3m_pct", z: "return_1y_pct",
                      label: "symbol", color: "daily_change_pct"),
                    axes: ["1M %", "3M %", "1Y %"], colorLabel: "1 day change",
                    colorCenteredAtZero: true, colorClipOutliers: true, colorAsPercent: true)
                }
              }.font(.subheadline)
              DisclosureGroup("All return periods", isExpanded: $showingMatrix) {
                if showingMatrix {
                  MatrixChart(
                    columns: catalog.value?.horizons.map(\.label) ?? [], rows: rows.map { $0.text("symbol") },
                    values: rows.map { row in (catalog.value?.horizons ?? []).map { row.number($0.column) } })
                }
              }.font(.subheadline)
            }
          }
          HStack {
            Text(selectedLens?.name ?? "Markets").font(.headline)
            Spacer()
            Text("\(returns.count)/\(rows.count) with \(selectedHorizon.label) history")
              .font(.caption2).foregroundStyle(.secondary)
          }
          ForEach(rows.map(MarketInstrument.init)) { instrument in
            let row = instrument.row
            VStack(spacing: 0) {
              NavigationLink {
                InvestigatorView(ticker: row.text("symbol"))
              } label: {
                VStack(alignment: .leading, spacing: 8) {
                  InstrumentHeading(
                    symbol: row.text("symbol"), name: row.text("company_name"), price: row.number("close"),
                    change: row.number(selectedHorizon.column), changeLabel: selectedHorizon.label,
                    disclosure: true)
                  if !row.text("direction").isEmpty {
                    Text(row.text("direction")).font(.caption2).foregroundStyle(.secondary)
                  }
                  Text("1D \(Format.percent(row.number("daily_change_pct"))) · 3M \(Format.percent(row.number("return_3m_pct")))")
                    .font(.caption2).foregroundStyle(.secondary)
                }.padding(.vertical, 6).contentShape(Rectangle())
              }.buttonStyle(.plain)
              Divider()
            }
          }
        } else if !data.loading, data.value != nil {
          Text(search.isEmpty ? "Price history unavailable for this lens" : "No matching symbols")
            .foregroundStyle(.secondary)
        }
      }.padding(20)
    }
    .navigationTitle("Markets").navigationBarTitleDisplayMode(.large)
    .searchable(text: $search, prompt: "Symbol or company").workbench()
    .researchContext("Market Explorer; lens=\(selectedLens?.name ?? ""); horizon=\(selectedHorizon?.label ?? "")")
    .task { await loadCatalog() }
    .task(id: selectionKey) { await load() }
    .task(id: candidateKey) { await loadCandidates() }
    .refreshable { await load(); await loadCandidates() }
    .onChange(of: mode) { _, _ in lensName = lenses.first?.name ?? "" }
  }
  private func loadCatalog() async {
    await catalog.load { try await session.api.get("/api/market-lenses") }
    if lensName.isEmpty { lensName = lenses.first?.name ?? "" }
  }
  private func load() async {
    guard let lens = selectedLens else { return }
    await data.load(key: selectionKey) {
      try await session.api.dataset("momentum_profiles",
        params: ["days": .number(3650), "symbols": .array(lens.symbols.map(JSONValue.string))])
    }
  }
  private func loadCandidates() async {
    guard let lens = selectedLens, let horizon = selectedHorizon else { return }
    await candidates.load(key: candidateKey) {
      try await session.api.dataset("market_opportunity_feed", params: [
        "business_filter": .string(mode == "business" ? lens.name : "All Market"), "selected_horizon_col": .string(horizon.column),
        "selected_horizon_label": .string(horizon.label),
        "symbols": .array(lens.symbols.map(JSONValue.string)), "limit": .number(1500),
      ])
    }
  }
}

private struct MarketInstrument: Identifiable {
  let row: Record
  var id: String { row.text("symbol") }
}
