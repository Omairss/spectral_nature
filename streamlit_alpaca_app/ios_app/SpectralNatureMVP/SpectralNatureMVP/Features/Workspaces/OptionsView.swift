import Charts
import SwiftUI

struct OptionsView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var chain = Resource<JSONValue>()
  @StateObject private var candidates = Resource<JSONValue>()
  @StateObject private var surface = Resource<[Record]>()
  @State private var symbol = ""
  @State private var ticker = ""
  @State private var expiration = ""
  @State private var kind = "Calls"
  @State private var section = "Chain"
  @State private var expected = ""
  @StateObject private var prices = Resource<Double>()
  private var underlying: Double? { prices.value }
  @State private var horizon = 30
  @State private var loadRevision = 0
  @State private var evaluatedLabel = ""
  private var expirations: [String] { chain.value?.array.first?.array.map(\.string) ?? [] }
  private var contracts: [Record] {
    guard let values = chain.value?.array, values.count >= 3 else { return [] }
    return values[kind == "Calls" ? 1 : 2].records
  }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        HStack {
          TextField("Symbol", text: $symbol).textInputAutocapitalization(.characters)
            .autocorrectionDisabled().submitLabel(.search).onSubmit { selectTicker() }
          Button("Load") { selectTicker() }.buttonStyle(.bordered).disabled(
            !validSymbol || chain.loading)
        }
        ResourceStatus(resource: prices) { Task { await loadUnderlying() } }
        ResourceStatus(resource: chain) { Task { await loadChain() } }
        if !ticker.isEmpty, chain.value != nil {
          InstrumentHeading(symbol: ticker, price: underlying, priceLabel: "Underlying price")
          Picker("Expiration", selection: $expiration) {
            ForEach(expirations, id: \.self) { Text($0) }
          }
          Picker("Contracts", selection: $kind) {
            Text("Calls").tag("Calls")
            Text("Puts").tag("Puts")
          }.pickerStyle(.segmented)
          Picker("Analysis", selection: $section) {
            ForEach(["Chain", "Scenario", "Surfaces"], id: \.self) { Text($0) }
          }.pickerStyle(.segmented)
          if section == "Chain" {
            Panel("Option chain") {
              WorkbookTable(
                rows: contracts,
                columns: [
                  .init(key: "contractSymbol", title: "Contract"),
                  .init(key: "strike", title: "Strike", style: .price),
                  .init(key: "bid", title: "Bid", style: .price),
                  .init(key: "ask", title: "Ask", style: .price),
                  .init(key: "impliedVolatility", title: "IV", style: .fraction),
                  .init(key: "openInterest", title: "Open interest", style: .number),
                  .init(key: "lastPrice", title: "Last", style: .price),
                  .init(key: "volume", title: "Volume", style: .number),
                  .init(key: "delta", title: "Delta", style: .number),
                  .init(key: "theta", title: "Theta", style: .number),
                ])
            }
            if contracts.contains(where: { $0.number("impliedVolatility") != nil }) {
              Panel("Implied volatility") {
                Chart(Array(contracts.enumerated()), id: \.offset) { _, row in
                  if let strike = row.number("strike"), let iv = row.number("impliedVolatility") {
                    PointMark(x: .value("Strike", strike), y: .value("IV %", iv * 100))
                      .foregroundStyle(Theme.violet)
                  }
                }.chartXAxisLabel("Strike").chartYAxisLabel("IV %").frame(height: 200)
              }
            }
          }
          if section != "Chain" {
            Panel("Scenario") {
              HStack {
                Text("Expected price").font(.subheadline)
                Spacer()
                TextField("Price", text: $expected).keyboardType(.decimalPad)
                  .multilineTextAlignment(
                    .trailing
                  ).frame(width: 120)
              }
              Picker("Horizon", selection: $horizon) {
                ForEach([7, 14, 30, 60, 90], id: \.self) { Text("\($0) days").tag($0) }
              }
              Button("Evaluate") { Task { await evaluate() } }.buttonStyle(.borderedProminent)
                .disabled(underlying == nil || (Double(expected) ?? 0) <= 0 || candidates.loading)
              ResourceStatus(resource: candidates) { Task { await evaluate() } }
              ResourceStatus(resource: surface) { Task { await evaluate() } }
              if section == "Scenario", let result = candidates.value {
                Text(evaluatedLabel).font(.caption).foregroundStyle(.secondary)
                if let best = result["candidates"].records.first {
                  Text(best.text("name").isEmpty ? best.text("contractSymbol") : best.text("name"))
                    .font(.headline)
                  Metrics(items: [
                    ("Premium", Format.money(best.number("premium"))),
                    ("Contract cost", Format.money(best.number("contracts_cost"))),
                    ("Projected P/L", Format.money(best.number("projected_pnl"))),
                    ("Projected return", Format.percent(best.number("projected_return_pct"))),
                    ("Breakeven", Format.price(best.number("breakeven"))),
                    ("Selection score", Format.number(best.number("selection_score"))),
                  ])
                  ScatterPlot(
                    rows: result["candidates"].records, x: "delta", y: "theta",
                    label: "contractSymbol", xTitle: "Delta", yTitle: "Theta", color: "vega",
                    size: "gamma")
                }
                WorkbookTable(
                  rows: result["candidates"].records,
                  columns: [
                    .init(key: "contractSymbol", title: "Contract"),
                    .init(key: "type", title: "Type"),
                    .init(key: "expiration", title: "Expiration"),
                    .init(key: "strike", title: "Strike", style: .price),
                    .init(key: "premium", title: "Premium", style: .price),
                    .init(key: "projected_return_pct", title: "Projected return", style: .percent),
                    .init(key: "delta", title: "Delta", style: .number),
                    .init(key: "theta", title: "Theta", style: .number),
                    .init(key: "selection_score", title: "Score", style: .number),
                  ])
                if !(result["summary"].dictionaryValue ?? [:]).isEmpty {
                  DisclosureGroup("Scenario summary") {
                    RecordDetails(record: result["summary"].dictionaryValue ?? [:])
                  }
                }
              }
            }
          }
          if section == "Surfaces", let rows = surface.value, !rows.isEmpty {
            Panel("\(kind) implied-volatility surface") {
              SpatialPlot(
                points: rows.filter {
                  $0.text("type").lowercased() == (kind == "Calls" ? "call" : "put")
                }.spatial(
                  x: "strike", y: "dte", z: "impliedVolatility", label: "contractSymbol",
                  color: "impliedVolatility", zScale: 100), axes: ["Strike", "DTE", "IV %"],
                surface: true, colorLabel: "IV")
            }
          }
          if section == "Surfaces", let result = candidates.value,
            !result["candidates"].records.isEmpty
          {
            Panel("Opportunity surface") {
              SpatialPlot(
                points: result["candidates"].records.spatial(
                  x: "strike", y: "dte", z: "projected_return_pct", label: "contractSymbol",
                  color: "selection_score"), axes: ["Strike", "DTE", "Projected return %"],
                colorLabel: "Selection score")
            }
          }
        }
      }.padding(20)
    }.navigationTitle("Option Strategizer").navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext(
        "Options; ticker=\(ticker); expiration=\(expiration); expected_price=\(expected); horizon=\(horizon) days"
      )
      .task(id: "\(ticker)|\(loadRevision)") {
        if !ticker.isEmpty {
          await loadUnderlying()
          await loadChain()
        }
      }
      .task(id: expiration) { if !ticker.isEmpty, !expiration.isEmpty { await loadChain() } }
  }
  private var validSymbol: Bool {
    symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased().range(
      of: "^[A-Z0-9.-]{1,12}$", options: .regularExpression) != nil
  }
  private func selectTicker() {
    guard validSymbol else { return }
    prices.value = nil
    expected = ""
    evaluatedLabel = ""
    chain.value = nil
    expiration = ""
    loadRevision += 1
    ticker = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
    candidates.value = nil
    surface.value = nil
  }
  private func loadUnderlying() async {
    let requestedTicker = ticker
    await prices.load(key: requestedTicker) {
      let snapshot: JSONValue = try await session.api.get(
        "/api/investigator/\(requestedTicker)", query: ["days": "180"])
      guard
        let current = snapshot["technical"]["close"].number
          ?? snapshot["forecast"]["current_price"].number, current > 0
      else { throw APIError.server(404, "Underlying price is unavailable.") }
      return current
    }
    guard requestedTicker == ticker, !Task.isCancelled else { return }
    expected = underlying.map { String(format: "%.2f", $0) } ?? ""
  }
  private func loadChain() async {
    await chain.load(key: "\(ticker)|\(expiration)") {
      try await session.api.dataset(
        "option_chain",
        params: [
          "ticker": .string(ticker), "expiration": expiration.isEmpty ? .null : .string(expiration),
        ])
    }
    if expiration.isEmpty { expiration = expirations.first ?? "" }
  }
  private func evaluate() async {
    guard let current = underlying, let price = Double(expected), price > 0 else { return }
    evaluatedLabel = "\(ticker) · Expected \(Format.price(price)) · \(horizon) days"
    let params: Record = [
      "ticker": .string(ticker), "expected_price": .number(price),
      "underlying_price": .number(current), "horizon_days": .number(Double(horizon)),
    ]
    let key = "\(ticker)|\(price)|\(horizon)"
    async let candidateLoad: Void = candidates.load(key: key) {
      try await session.api.dataset(
        "option_candidates", params: params)
    }
    async let surfaceLoad: Void = surface.load(key: key) {
      try await session.api.dataset("option_surface", params: params)
    }
    _ = await (candidateLoad, surfaceLoad)
  }
}
