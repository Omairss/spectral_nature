import SwiftUI

struct InvestigatorSearch: View {
  @State private var ticker = ""
  @State private var selectedTicker = ""
  @State private var showing = false
  var body: some View {
    Form {
      TextField("Symbol", text: $ticker).textInputAutocapitalization(.characters)
        .autocorrectionDisabled().submitLabel(.search).onSubmit { open() }
      Button("Investigate", action: open).disabled(!valid)
    }.scrollContentBackground(.hidden).workbench().navigationTitle("Stock Investigator")
      .navigationDestination(isPresented: $showing) { InvestigatorView(ticker: selectedTicker) }
  }
  private var normalized: String {
    ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
  }
  private var valid: Bool {
    normalized.range(of: "^[A-Z0-9.-]{1,12}$", options: .regularExpression) != nil
  }
  private func open() {
    guard valid else { return }
    selectedTicker = normalized
    showing = true
  }
}

struct InvestigatorView: View {
  let ticker: String
  @EnvironmentObject private var session: AppSession
  @StateObject private var snapshot = Resource<JSONValue>()
  @StateObject private var artifact = Resource<JSONValue>()
  @StateObject private var prices = Resource<JSONValue>()
  @State private var days = 365
  @State private var tab = "Overview"
  @State private var metric = ""
  private let tabs = ["Overview", "Price", "Pullback", "Income", "Balance", "Cash flow", "Sources"]
  private var priceRows: [Record] {
    prices.value?["datasets"].dictionaryValue?.sorted { $0.key < $1.key }.first?.value["records"]
      .records ?? []
  }
  private var latestPrice: Record? {
    priceRows.filter { $0.number("close") != nil }.max {
      $0.text("timestamp") < $1.text("timestamp")
    }
  }
  private var quote: Double? {
    latestPrice?.number("close") ?? snapshot.value?["technical"]["close"].number
      ?? snapshot.value?["forecast"]["current_price"].number
  }
  private var definition: (String, Record)? {
    switch tab {
    case "Price":
      return (
        "technical_price_channel", ["ticker": .string(ticker), "days": .number(Double(days))]
      )
    case "Pullback":
      return ("technical_pullback", ["ticker": .string(ticker), "days": .number(Double(days))])
    case "Income", "Balance", "Cash flow":
      return (
        "fundamental_statement",
        [
          "ticker": .string(ticker),
          "statement": .string(tab == "Cash flow" ? "cashflow" : tab.lowercased()),
        ]
      )
    default: return nil
    }
  }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        InstrumentHeading(
          symbol: ticker, name: snapshot.value?["company"]["name"].string ?? "", price: quote)
        if let date = latestPrice.flatMap({ Format.date($0.text("timestamp")) }) {
          Text(Format.observationDay(date)).font(.caption2).foregroundStyle(.secondary)
        }
        HStack {
          Picker("Analysis", selection: $tab) { ForEach(tabs, id: \.self) { Text($0) } }
          Spacer()
          Picker("History", selection: $days) {
            ForEach([60, 180, 365, 720], id: \.self) { Text("\($0) days").tag($0) }
          }
        }
        if tab == "Overview" || tab == "Sources" {
          ResourceStatus(resource: snapshot) { Task { await loadSnapshot() } }
          if let view = snapshot.value {
            if view["status"].string != "available" {
              Text("Company data unavailable").foregroundStyle(.secondary)
            } else if tab == "Sources" {
              ForEach(Array(view["articles"].records.enumerated()), id: \.offset) { _, article in
                Panel(article.text("headline")) {
                  Text(article.text("summary")).font(.subheadline)
                  Text("\(article.text("source")) · \(article.text("published_at"))").font(.caption)
                    .foregroundStyle(.secondary)
                  if let url = URL(string: article.text("url")),
                    ["http", "https"].contains(url.scheme ?? "")
                  {
                    Link("Read source", destination: url)
                  }
                }
              }
            } else {
              overview(view)
            }
          }
        } else if tab == "Price" {
          ResourceStatus(resource: prices) { Task { await loadPrices() } }
          if let visualization = prices.value { chart(visualization) }
        } else {
          ResourceStatus(resource: artifact) { Task { await loadArtifact() } }
          if let visualization = artifact.value { chart(visualization) }
        }
      }.padding(20)
    }.navigationTitle(ticker).navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext("Stock Investigator; ticker=\(ticker); history=\(days) days; view=\(tab)")
      .task(id: days) { await loadSnapshot() }
      .task(id: days) { await loadPrices() }
      .task(id: "\(tab)|\(days)") { await loadArtifact() }
      .refreshable {
        await loadPrices()
        if definition == nil { await loadSnapshot() } else { await loadArtifact() }
      }
  }
  @ViewBuilder private func overview(_ view: JSONValue) -> some View {
    let company = view["company"]
    if !company["business_lens"].string.isEmpty {
      Text(company["business_lens"].string).font(.caption).foregroundStyle(Theme.violet)
    }
    let technical = view["technical"]
    Panel {
      Metrics(items: [
        ("Support", Format.price(technical["channel_support"].number)),
        ("Resistance", Format.price(technical["channel_resistance"].number)),
        ("All-time high", Format.price(technical["ath"].number)),
      ])
    }
    if !company["headline"].string.isEmpty { Text(company["headline"].string).font(.headline) }
    if !company["summary"].string.isEmpty { MarkdownView(text: company["summary"].string) }
    ForEach(
      [("why_now", "Why now"), ("management_signal", "Management"), ("background", "Business")],
      id: \.0
    ) { key, title in
      if !company[key].string.isEmpty { Panel(title) { MarkdownView(text: company[key].string) } }
    }
    let forecast = view["forecast"]
    if !(forecast.dictionaryValue ?? [:]).isEmpty {
      Panel("Forecast") {
        Metrics(items: [
          ("1W up", Format.percent(forecast["up_probability"].number, fraction: true)),
          ("Breakout", Format.percent(forecast["breakout_probability"].number, fraction: true)),
          ("Analogs", Format.number(forecast["analog_count"].number)),
          ("Median price", Format.price(forecast["terminal_band"]["p50"].number)),
          ("Expected return", Format.percent(forecast["expected_return_pct"].number)),
          ("Lower band", Format.price(forecast["terminal_band"]["p10"].number)),
          ("Upper band", Format.price(forecast["terminal_band"]["p90"].number)),
        ])
      }
    }
  }
  @ViewBuilder private func chart(_ visualization: JSONValue) -> some View {
    let dataset =
      visualization["datasets"].dictionaryValue?.sorted { $0.key < $1.key }.first?.value ?? .null
    let rows = dataset["records"].records
    Panel(tab) {
      if tab == "Price" {
        TimeSeriesChart(
          rows: rows,
          series: [
            ("close", "Close"), ("channel_support", "Support"),
            ("channel_resistance", "Resistance"), ("ath", "ATH"),
          ], height: 260, price: true)
      } else if tab == "Pullback" {
        TimeSeriesChart(rows: rows, series: [("pullback_from_ath_pct", "Pullback %")], height: 240)
      } else {
        let metrics = Array(Set(rows.map { $0.text("metric") })).sorted()
        let chosen = metrics.contains(metric) ? metric : metrics.first ?? ""
        Picker("Metric", selection: $metric) { ForEach(metrics, id: \.self) { Text($0) } }
          .onAppear { if !metrics.contains(metric) { metric = metrics.first ?? "" } }
        TimeSeriesChart(
          rows: rows.filter { $0.text("metric") == chosen }, date: "report_date",
          series: [("value", chosen)], height: 240)
      }
      DisclosureGroup("Data and provenance") {
        let columns = dataset["columns"].records.map {
          WorkbookColumn(
            key: $0.text("name"), title: $0.text("name").replacingOccurrences(of: "_", with: " "),
            style: $0.text("logical_type") == "number" ? .number : .text)
        }
        WorkbookTable(rows: Array(rows.suffix(120).reversed()), columns: columns)
        Text(visualization["provenance"]["datasets"].array.map(\.string).joined(separator: ", "))
          .font(.caption2).foregroundStyle(.secondary)
      }.font(.caption)
    }
  }
  private func loadSnapshot() async {
    await snapshot.load(key: "\(ticker)|\(days)") {
      try await session.api.get("/api/investigator/\(ticker)", query: ["days": String(days)])
    }
  }
  private func loadArtifact() async {
    guard tab != "Price" else { return }
    guard let (name, params) = definition else { return }
    await artifact.load(key: "\(ticker)|\(tab)|\(days)") {
      let response: JSONValue = try await session.api.post(
        "/api/visualizations/\(name)", body: ["params": .object(params)])
      return response["payload"]
    }
  }
  private func loadPrices() async {
    await prices.load(key: "\(ticker)|\(days)", clearOnFailure: true) {
      let response: JSONValue = try await session.api.post(
        "/api/visualizations/technical_price_channel",
        body: ["params": .object(["ticker": .string(ticker), "days": .number(Double(days))])])
      return response["payload"]
    }
  }
}
