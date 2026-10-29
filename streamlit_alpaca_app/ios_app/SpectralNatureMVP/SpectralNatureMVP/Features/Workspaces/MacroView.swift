import SwiftUI

private struct MacroWorkbook {
  let summary: [Record]
  let observations: [String: [Record]]
  let correlations: [Record]
  let index: [Record]
  let blurbs: Record
  init(_ payload: JSONValue) {
    summary = payload["summary"].records
    observations = Dictionary(
      grouping: payload["observations"].records, by: { $0.text("series_id") })
    correlations = payload["correlations"].records
    index = payload["series_index"].records
    blurbs = payload["category_blurbs"].dictionaryValue ?? [:]
  }
  var categories: [String] { Array(Set(summary.map { $0.text("category") })).sorted() }
}

struct MacroView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.scenePhase) private var scenePhase
  @StateObject private var resource = Resource<MacroWorkbook>()
  @StateObject private var outlook = Resource<Record>()
  @State private var category = ""
  @State private var years = 10
  @State private var overlay = "combined"
  @State private var section = "Indicators"
  @State private var search = ""
  @State private var release = ""
  @State private var selected = ""
  @State private var inspecting = false
  private var selectedCategory: String {
    resource.value?.categories.contains(category) == true
      ? category : resource.value?.categories.first ?? ""
  }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        Panel("Economic outlook") {
          if let summary = outlook.value, summary.text("status") == "ok" {
            if !summary.text("headline").isEmpty {
              Text(summary.text("headline")).font(.headline)
            }
            if let date = Format.date(summary["materialized"]?["generated_at_utc"].string ?? "") {
              Text(date.formatted(date: .abbreviated, time: .shortened))
                .font(.caption).foregroundStyle(.secondary)
            }
            MarkdownView(text: summary.text("summary_markdown"))
            let items = summary["watch_items"]?.array.map(\.string).filter { !$0.isEmpty } ?? []
            if !items.isEmpty {
              Text("Watch next").font(.subheadline.weight(.semibold))
              MarkdownView(text: items.map { "- \($0)" }.joined(separator: "\n"))
            }
          }
          ResourceStatus(resource: outlook) { Task { await loadOutlook() } }
          if outlook.value == nil && !outlook.loading && outlook.error.isEmpty {
            ProgressView().accessibilityLabel("Loading economic outlook")
          }
        }
        HStack {
          Picker("Category", selection: Binding(get: { selectedCategory }, set: { category = $0 }))
          {
            ForEach(resource.value?.categories ?? [], id: \.self) { Text($0) }
          }
          Spacer()
          Picker("Lookback", selection: $years) {
            ForEach([3, 5, 10, 15, 20], id: \.self) { Text("\($0) years").tag($0) }
          }
        }
        Picker("View", selection: $section) {
          ForEach(["Indicators", "Readings", "Series"], id: \.self) { Text($0) }
        }.pickerStyle(.segmented)
        if section == "Indicators" {
          Picker("Change", selection: $overlay) {
            Text("Raw + smoothed").tag("combined")
            Text("Smoothed").tag("smoothed")
            Text("Raw").tag("raw")
            Text("Off").tag("off")
          }
        }
        ResourceStatus(resource: resource) { Task { await load() } }
        if let macro = resource.value {
          let rows = macro.summary.filter { $0.text("category") == selectedCategory }
          if section == "Series" {
            TextField("Search series", text: $search).textFieldStyle(.roundedBorder)
            Picker("Release", selection: $release) {
              Text("All releases").tag("")
              ForEach(
                Array(Set(macro.index.map { $0.text("release_name") }.filter { !$0.isEmpty }))
                  .sorted(), id: \.self
              ) { Text($0) }
            }
            ForEach(
              Array(
                macro.index.filter { row in
                  (release.isEmpty || row.text("release_name") == release)
                    && (search.isEmpty
                      || ["series_id", "title", "notes"].contains {
                        row.text($0).localizedCaseInsensitiveContains(search)
                      })
                }.enumerated()), id: \.offset
            ) { _, row in
              Button {
                selected = row.text("series_id")
                inspecting = true
              } label: {
                VStack(alignment: .leading, spacing: 5) {
                  Text(row.text("title")).font(.subheadline)
                  Text("\(row.text("series_id")) · \(row.text("release_name"))").font(.caption)
                    .foregroundStyle(.secondary)
                  Text("\(row.text("frequency")) · \(row.text("units"))").font(.caption2)
                    .foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .leading)
              }.buttonStyle(.plain)
              Divider()
            }
          } else if section == "Readings" {
            Panel("Latest readings") {
              WorkbookTable(
                rows: rows,
                columns: [
                  .init(key: "indicator", title: "Indicator"),
                  .init(key: "units_short", title: "Units"),
                  .init(key: "latest_value", title: "Latest", style: .number),
                  .init(key: "latest_rate_of_change", title: "Change", style: .number),
                  .init(key: "rate_of_change_label", title: "Change units"),
                  .init(key: "yoy_delta", title: "YoY level change", style: .number),
                  .init(key: "latest_date", title: "Observed", style: .date),
                ])
            }
          } else {
            if let description = macro.blurbs[selectedCategory]?.string, !description.isEmpty {
              MarkdownView(text: description)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in indicator(row, macro) }
            let correlations = macro.correlations.filter { $0.text("category") == selectedCategory }
            if !correlations.isEmpty {
              Panel("Change correlation") {
                Text("Monthly aligned · \(years) years").font(.caption).foregroundStyle(.secondary)
                CorrelationGrid(
                  labels: rows.map { $0.text("indicator") }, values: matrix(rows, correlations))
                DisclosureGroup("Aligned observations") {
                  WorkbookTable(
                    rows: correlations,
                    columns: [
                      .init(key: "left_series_id", title: "Left"),
                      .init(key: "right_series_id", title: "Right"),
                      .init(key: "correlation", title: "Correlation", style: .number),
                      .init(key: "observations", title: "Months", style: .number),
                    ])
                }
              }
            }
          }
        }
      }.padding(20)
    }.navigationTitle("Broad Economy").navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext(
        "Broad Economy; category=\(selectedCategory); lookback=\(years) years; series=\(selected)"
      )
      .task(id: years) { await load() }
      .task(id: scenePhase) {
        guard scenePhase == .active else { return }
        repeat {
          await loadOutlook()
          do { try await Task.sleep(for: .seconds(300)) } catch { return }
        } while !Task.isCancelled
      }
      .refreshable {
        async let workbook: () = load()
        async let summary: () = loadOutlook()
        _ = await (workbook, summary)
      }
      .sheet(isPresented: $inspecting) {
        NavigationStack {
          ScrollView {
            if let macro = resource.value {
              VStack(alignment: .leading, spacing: 20) {
                if let row = macro.summary.first(where: { $0.text("series_id") == selected }) {
                  indicator(row, macro)
                } else {
                  TimeSeriesChart(
                    rows: macro.observations[selected] ?? [], date: "date",
                    series: [("value", "Level")])
                  if let row = macro.index.first(where: { $0.text("series_id") == selected }) {
                    RecordDetails(record: row)
                  }
                }
              }.padding(20)
            }
          }.navigationTitle(selected).navigationBarTitleDisplayMode(.inline).workbench()
            .toolbar {
              ToolbarItem(placement: .cancellationAction) { Button("Close") { inspecting = false } }
            }
            .researchContext("Macro series=\(selected); lookback=\(years) years")
        }.preferredColorScheme(.dark)
      }
  }
  private func indicator(_ row: Record, _ macro: MacroWorkbook) -> some View {
    Panel(row.text("indicator")) {
      Metrics(items: [
        (row.text("units_short"), Format.number(row.number("latest_value"))),
        ("Observed", Format.date(row.text("latest_date")).map(Format.observationDay) ?? "—"),
        (row.text("rate_of_change_label"), Format.number(row.number("latest_rate_of_change"))),
        ("YoY level change", Format.number(row.number("yoy_delta"))),
      ])
      TimeSeriesChart(
        rows: macro.observations[row.text("series_id")] ?? [], date: "date",
        series: [("value", "Level")], height: 170)
      if overlay != "off" {
        Text(row.text("rate_of_change_label")).font(.caption).foregroundStyle(Theme.violet)
        TimeSeriesChart(
          rows: macro.observations[row.text("series_id")] ?? [], date: "date",
          series: (overlay == "combined" || overlay == "raw" ? [("rate_of_change", "Raw")] : [])
            + (overlay == "combined" || overlay == "smoothed"
              ? [("smoothed_rate_of_change", row.text("smoothing_label"))] : []),
          height: 130)
      }
      Text("\(row.text("series_id")) · \(row.text("frequency_short"))").font(.caption2)
        .foregroundStyle(.secondary)
    }
  }
  private func load() async {
    await resource.load(key: String(years)) {
      let payload: JSONValue = try await session.api.dataset(
        "fred_dashboard", params: ["years": .number(Double(years))])
      return MacroWorkbook(payload)
    }
  }
  private func loadOutlook() async {
    await outlook.load {
      let payload: JSONValue = try await session.api.dataset(
        "page_agentic_summary", params: ["surface": .string("Broad Economy")])
      guard let summary = payload.dictionaryValue, summary.text("status") == "ok",
        !summary.text("headline").isEmpty || !summary.text("summary_markdown").isEmpty else {
        throw APIError.server(503, "Economic outlook unavailable.")
      }
      return summary
    }
  }
  private func matrix(_ rows: [Record], _ correlations: [Record]) -> [[Double?]] {
    let lookup = Dictionary(
      correlations.map {
        ("\($0.text("left_series_id"))|\($0.text("right_series_id"))", $0.number("correlation"))
      }, uniquingKeysWith: { first, _ in first })
    return rows.map { left in
      rows.map { right in lookup["\(left.text("series_id"))|\(right.text("series_id"))"] ?? nil }
    }
  }
}
