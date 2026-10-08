import Charts
import SwiftUI

enum Theme {
  static let background = Color(red: 0.035, green: 0.043, blue: 0.051)
  static let panel = Color(red: 0.065, green: 0.075, blue: 0.086)
  static let border = Color.white.opacity(0.10)
  static let teal = Color(red: 0.32, green: 0.84, blue: 0.78)
  static let violet = Color(red: 0.70, green: 0.62, blue: 0.96)
  static let coral = Color(red: 0.94, green: 0.55, blue: 0.45)
  static let blue = Color(red: 0.42, green: 0.72, blue: 1)
  static func tone(_ value: Double?) -> Color {
    guard let value, value != 0 else { return .secondary }
    return value > 0 ? teal : coral
  }
}

enum Format {
  private static let iso = ISO8601DateFormatter()
  private static let fractionalISO: ISO8601DateFormatter = {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter
  }()
  private static let utcDay: DateFormatter = {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = TimeZone(secondsFromGMT: 0)
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter
  }()
  static func number(_ value: Double?, digits: Int = 2) -> String {
    guard let value, value.isFinite else { return "—" }
    return value.formatted(.number.precision(.fractionLength(0...digits)))
  }
  static func money(_ value: Double?, compact: Bool = false) -> String {
    guard let value, value.isFinite else { return "—" }
    if compact {
      for (scale, suffix) in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]
      where abs(value) >= scale {
        return "$" + number(value / scale, digits: 1) + suffix
      }
    }
    return value.formatted(
      .currency(code: "USD").precision(.fractionLength(abs(value) >= 1000 ? 0 : 2)))
  }
  static func percent(_ value: Double?, fraction: Bool = false) -> String {
    guard let value, value.isFinite else { return "—" }
    let percentage = value * (fraction ? 100 : 1)
    return (percentage > 0 ? "+" : "") + number(percentage, digits: 2) + "%"
  }
  static func date(_ raw: String) -> Date? {
    if raw.count == 10 { return utcDay.date(from: raw) }
    return iso.date(from: raw) ?? fractionalISO.date(from: raw)
      ?? utcDay.date(from: String(raw.prefix(10)))
  }
  static func day(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.string(from: date)
  }
  static func observationDay(_ date: Date) -> String { utcDay.string(from: date) }
}

extension Dictionary where Key == String, Value == JSONValue {
  func text(_ key: String) -> String { self[key]?.string ?? "" }
  func number(_ key: String) -> Double? { self[key]?.number }
  func rows(_ key: String) -> [Record] { self[key]?.records ?? [] }
  func object(_ key: String) -> Record { self[key]?.dictionaryValue ?? [:] }
}

struct Brand: View {
  var body: some View {
    HStack(spacing: 10) {
      HStack(spacing: 3) {
        ForEach(Array([10.0, 20, 30, 16, 24, 10].enumerated()), id: \.offset) { _, height in
          Capsule().fill(Theme.teal).frame(width: 3, height: height)
        }
      }.accessibilityHidden(true)
      Text("SPECTRAL NATURE").font(.system(size: 12, weight: .semibold, design: .rounded)).tracking(
        2)
    }.accessibilityElement(children: .combine)
  }
}

struct Panel<Content: View>: View {
  let title: String
  @ViewBuilder var content: () -> Content
  init(_ title: String = "", @ViewBuilder content: @escaping () -> Content) {
    self.title = title
    self.content = content
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      if !title.isEmpty { Text(title).font(.headline).foregroundStyle(.primary) }
      content()
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(16)
    .background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
  }
}

struct Metrics: View {
  let items: [(String, String)]
  var body: some View {
    LazyVGrid(
      columns: [
        GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading),
      ], alignment: .leading, spacing: 16
    ) {
      ForEach(Array(items.enumerated()), id: \.offset) { _, item in
        VStack(alignment: .leading, spacing: 5) {
          Text(item.0).font(.caption).foregroundStyle(.secondary)
          Text(item.1).font(.system(.body, design: .rounded).weight(.medium)).monospacedDigit()
            .textSelection(.enabled)
        }
      }
    }
  }
}

struct Failure: View {
  let message: String
  var retry: (() -> Void)? = nil
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      Label(message, systemImage: "exclamationmark.circle").font(.subheadline).foregroundStyle(
        Theme.coral)
      if let retry { Button("Try again", action: retry).buttonStyle(.bordered) }
    }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8).accessibilityElement(
      children: .contain)
  }
}

@MainActor
final class Resource<Value>: ObservableObject {
  @Published var value: Value?
  @Published var loading = false
  @Published var error = ""
  private var generation = 0
  private var currentKey = ""
  func load(key: String = "", _ operation: () async throws -> Value) async {
    generation += 1
    let request = generation
    if key != currentKey {
      value = nil
      currentKey = key
    }
    loading = true
    error = ""
    defer { if request == generation { loading = false } }
    do {
      let result = try await operation()
      guard request == generation, !Task.isCancelled else { return }
      value = result
    } catch {
      guard request == generation, !Task.isCancelled else { return }
      self.error = error.localizedDescription
    }
  }
}

struct ResourceStatus<Value>: View {
  @ObservedObject var resource: Resource<Value>
  let retry: () -> Void
  var body: some View {
    if resource.loading {
      ProgressView().frame(maxWidth: .infinity).padding(16).accessibilityLabel("Loading")
    }
    if !resource.error.isEmpty { Failure(message: resource.error, retry: retry) }
  }
}

struct AccessGate<Content: View>: View {
  @EnvironmentObject private var session: AppSession
  @ViewBuilder let content: () -> Content
  var body: some View {
    if session.authenticated {
      content()
    } else if session.restoring {
      ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.background)
        .accessibilityLabel("Checking session")
    } else {
      VStack(spacing: 22) {
        Image(systemName: "person.crop.circle").font(.system(size: 36)).foregroundStyle(Theme.teal)
        Button("Sign in") { session.showingLogin = true }.buttonStyle(.borderedProminent)
      }.frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.background)
    }
  }
}

struct WorkbookColumn {
  enum Style { case text, number, money, percent, fraction, date }
  let key: String
  let title: String
  var style: Style = .text
  func value(_ row: Record) -> String {
    switch style {
    case .text:
      if case let .array(values) = row[key] { return values.map(\.string).joined(separator: ", ") }
      return row.text(key).isEmpty ? "—" : row.text(key)
    case .number: return Format.number(row.number(key))
    case .money: return Format.money(row.number(key))
    case .percent: return Format.percent(row.number(key))
    case .fraction: return Format.percent(row.number(key), fraction: true)
    case .date:
      return Format.date(row.text(key)).map(Format.observationDay) ?? "—"
    }
  }
}

struct WorkbookTable: View {
  let rows: [Record]
  let columns: [WorkbookColumn]
  var body: some View {
    if rows.isEmpty {
      Text("No rows").font(.subheadline).foregroundStyle(.secondary)
    } else {
      ScrollView(.horizontal) {
        if rows.count <= 10 {
          VStack(alignment: .leading, spacing: 0) { contents }
            .frame(width: tableWidth, alignment: .leading)
        } else {
          ScrollView(.vertical) {
            LazyVStack(alignment: .leading, spacing: 0) { contents }
              .frame(width: tableWidth, alignment: .leading)
          }.frame(height: 540)
        }
      }
    }
  }
  private var tableWidth: CGFloat {
    columns.reduce(CGFloat(0)) { $0 + ($1.style == .text ? 150 : 100) + 16 } - 16
  }
  @ViewBuilder private var contents: some View {
    HStack(spacing: 16) {
      ForEach(columns, id: \.key) { column in
        Text(column.title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
          .frame(
            width: column.style == .text ? 150 : 100, alignment: .leading)
      }
    }.padding(.vertical, 10)
    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
      Divider().overlay(Theme.border)
      HStack(alignment: .top, spacing: 16) {
        ForEach(columns, id: \.key) { column in
          Text(column.value(row)).font(.caption).monospacedDigit().frame(
            width: column.style == .text ? 150 : 100, alignment: .leading
          ).textSelection(.enabled)
        }
      }.padding(.vertical, 12)
    }
  }
}

struct RecordDetails: View {
  let record: Record
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      ForEach(record.keys.sorted(), id: \.self) { key in
        let value = record[key] ?? .null
        switch value {
        case let .object(fields):
          DisclosureGroup(key.replacingOccurrences(of: "_", with: " ").capitalized) {
            RecordDetails(record: fields)
          }
        case let .array(values):
          DisclosureGroup(key.replacingOccurrences(of: "_", with: " ").capitalized) {
            ForEach(Array(values.enumerated()), id: \.offset) { _, item in
              if let fields = item.dictionaryValue {
                RecordDetails(record: fields)
                Divider()
              } else {
                Text(item.string).font(.caption).textSelection(.enabled)
              }
            }
          }
        default:
          VStack(alignment: .leading, spacing: 3) {
            Text(key.replacingOccurrences(of: "_", with: " ").capitalized).font(.caption2)
              .foregroundStyle(.secondary)
            Text(value == .null ? "—" : value.string).font(.caption).textSelection(.enabled)
          }
        }
      }
    }
  }
}

struct TimeSeriesChart: View {
  struct Point: Identifiable {
    let id: Int
    let date: Date
    let value: Double
    let series: String
    let segment: Int
  }
  let points: [Point]
  var height: CGFloat = 200
  @State private var selectedDate: Date?
  init(
    rows: [Record], date: String = "timestamp", series: [(String, String)], height: CGFloat = 200
  ) {
    self.height = height
    var output: [Point] = []
    let ordered = rows.sorted { $0.text(date) < $1.text(date) }
    for (key, label) in series {
      var segment = 0
      for row in ordered {
        guard let date = Format.date(row.text(date)), let value = row.number(key) else {
          segment += 1
          continue
        }
        output.append(
          Point(id: output.count, date: date, value: value, series: label, segment: segment))
      }
    }
    points = output
  }
  init(trend: [TrendPoint], label: String, height: CGFloat = 150) {
    self.init(
      rows: trend.map { ["date": .string($0.date), "value": .number($0.value)] }, date: "date",
      series: [("value", label)], height: height)
  }
  var body: some View {
    if points.isEmpty {
      Text("History unavailable").font(.caption).foregroundStyle(.secondary)
    } else {
      VStack(alignment: .leading, spacing: 8) {
        if let selectedDate,
          let point = points.min(by: {
            abs($0.date.timeIntervalSince(selectedDate))
              < abs($1.date.timeIntervalSince(selectedDate))
          })
        {
          Text(
            "\(Format.observationDay(point.date)) · \(point.series) \(Format.number(point.value))"
          ).font(.caption).monospacedDigit()
        }
        Chart {
          ForEach(points) { point in
            LineMark(
              x: .value("Date", point.date), y: .value("Value", point.value),
              series: .value("Segment", "\(point.series):\(point.segment)")
            )
            .foregroundStyle(by: .value("Series", point.series))
            .lineStyle(StrokeStyle(lineWidth: 1.8))
          }
          if let selectedDate {
            RuleMark(x: .value("Selected", selectedDate)).foregroundStyle(.secondary.opacity(0.3))
          }
        }
        .chartForegroundStyleScale(range: [Theme.teal, Theme.violet, Theme.blue, Theme.coral])
        .chartXSelection(value: $selectedDate)
        .chartYAxis {
          AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
            AxisGridLine()
            AxisValueLabel {
              if let number = value.as(Double.self) {
                Text(number.formatted(.number.notation(.compactName)))
              }
            }
          }
        }
        .chartXAxis {
          AxisMarks(values: .automatic(desiredCount: 3)) { _ in
            AxisGridLine()
            AxisValueLabel(anchor: .top)
          }
        }
        .chartXScale(range: .plotDimension(startPadding: 12, endPadding: 24))
        .chartYScale(domain: .automatic(includesZero: false))
        .chartLegend(position: .bottom, alignment: .leading)
        .frame(height: height)
        .accessibilityLabel("Time series, \(points.count) observations. Drag to inspect values.")
      }
    }
  }
}

struct CorrelationGrid: View {
  let labels: [String]
  let values: [[Double?]]
  var body: some View {
    ScrollView(.horizontal) {
      VStack(alignment: .leading, spacing: 3) {
        HStack(spacing: 3) {
          Color.clear.frame(width: 90, height: 28)
          ForEach(Array(labels.enumerated()), id: \.offset) { _, label in
            Text(label).font(.system(size: 9)).lineLimit(2).frame(width: 54, height: 28)
          }
        }
        ForEach(Array(labels.enumerated()), id: \.offset) { i, label in
          HStack(spacing: 3) {
            Text(label).font(.caption2).lineLimit(2).frame(width: 90, alignment: .leading)
            ForEach(Array(labels.enumerated()), id: \.offset) { j, other in
              let value = i < values.count && j < values[i].count ? values[i][j] : nil
              Text(Format.number(value)).font(.system(size: 10, design: .monospaced)).frame(
                width: 54, height: 34
              )
              .background(
                Theme.tone(value).opacity(abs(value ?? 0) * 0.5 + 0.08),
                in: RoundedRectangle(cornerRadius: 3)
              )
              .accessibilityLabel("\(label), \(other): \(Format.number(value))")
            }
          }
        }
      }
    }
  }
}

extension View {
  func workbench() -> some View {
    self.background(Theme.background).toolbarBackground(Theme.background, for: .navigationBar)
      .toolbarBackground(.visible, for: .navigationBar)
  }
  func researchContext(_ context: String) -> some View {
    modifier(ResearchContext(context: context))
  }
}
