import Charts
import SwiftUI

extension JSONValue {
  var researchChartID: String { self["artifact_id"].string }
}

/// Native rendering of the same validated datasets/traces that produce Plotly.
/// A segment identity keeps missing observations from joining across a gap.
private struct ResearchPlot {
  struct Point: Identifiable {
    let id: Int
    let x: Double
    let y: Double?
    let lower: Double?
    let upper: Double?
    let open: Double?
    let segment: String
    let row: Record
  }
  struct Series: Identifiable {
    let id: Int
    let name: String
    let kind: String
    let points: [Point]
    let left: String
    let right: String
  }
  let series: [Series]
  let xType: String
  let categories: [String]
  let xTitle: String
  let yTitle: String
  let xPercent: Bool
  let yPercent: Bool
  let matrix: [Record]
  let spansYears: Bool

  init(_ artifact: JSONValue) {
    let chart = artifact["spec"]["chart"]
    let traces = chart["traces"].array
    xTitle = chart["layout"]["xaxis"]["title"]["text"].string
    yTitle = chart["layout"]["yaxis"]["title"]["text"].string
    xPercent = chart["metadata"]["x_format"].string == "percent" || chart["layout"]["xaxis"]["tickformat"].string.hasSuffix("%")
    yPercent = chart["metadata"]["y_format"].string == "percent" || chart["layout"]["yaxis"]["tickformat"].string.hasSuffix("%")
    var groups: [(JSONValue, [Record])] = []
    for trace in traces {
      let dataset = trace["dataset"].string
      let rows = artifact["datasets"][dataset.isEmpty ? "primary" : dataset]["records"].records
      let filtered = rows.filter { row in
        (trace["where"].dictionaryValue ?? [:]).allSatisfy { key, expected in
          guard let actual = row[key] else { return false }
          if case .array(let values) = expected { return values.contains(actual) }
          return actual == expected
        }
      }
      groups.append((trace, filtered))
    }
    let allX = groups.flatMap { trace, rows in rows.compactMap { $0[trace["x"].string] } }
    let declared = chart["metadata"]["x_type"].string
    xType = !declared.isEmpty ? declared : (allX.allSatisfy { $0.number != nil } ? "number" :
      (allX.allSatisfy { Format.date($0.string) != nil } ? "date" : "category"))
    var names: [String] = []
    if xType == "category" {
      for x in allX where !names.contains(x.string) { names.append(x.string) }
    }
    categories = names
    var output: [Series] = []
    var cells: [Record] = []
    for (index, pair) in groups.enumerated() {
      let (trace, rows) = pair
      let kind = trace["trace_type"].string
      if kind == "heatmap" {
        cells = rows.map { ["x": $0[trace["x"].string] ?? .null,
                           "y": $0[trace["y"].string] ?? .null,
                           "z": $0[trace["z"].string] ?? .null] }
        continue
      }
      var points: [Point] = []
      var segment = 0
      for (i, row) in rows.enumerated() {
        let xValue = row[trace["x"].string] ?? .null
        let x: Double?
        if xType == "date" { x = Format.date(xValue.string)?.timeIntervalSince1970 }
        else if xType == "category" { x = names.firstIndex(of: xValue.string).map(Double.init) }
        else { x = xValue.number }
        let y = row.number(trace[kind == "candlestick" ? "close" : "y"].string)
        let lower = row.number(trace[kind == "candlestick" ? "low" : "lower"].string)
        let upper = row.number(trace[kind == "candlestick" ? "high" : "upper"].string)
        let open = row.number(trace["open"].string)
        if x == nil || (kind == "band" ? lower == nil || upper == nil :
          (kind == "candlestick" ? open == nil || lower == nil || upper == nil || y == nil : y == nil)) {
          segment += 1
          continue
        }
        points.append(Point(id: i, x: x!, y: y, lower: lower, upper: upper, open: open,
                            segment: "\(index):\(segment)", row: row))
      }
      output.append(Series(id: index, name: trace["name"].string, kind: kind, points: points,
        left: trace["options"]["left"].string, right: trace["options"]["right"].string))
    }
    series = output
    matrix = cells
    let coordinates = output.flatMap { $0.points.map(\.x) }
    spansYears = (coordinates.max() ?? 0) - (coordinates.min() ?? 0) > 370 * 86_400
  }

  func xLabel(_ value: Double, axis: Bool = false) -> String {
    if xType == "date" {
      let date = Date(timeIntervalSince1970: value)
      return axis ? Format.historyDate(date, years: spansYears) : Format.observationDay(date)
    }
    if xType == "category" {
      let i = Int(value.rounded())
      return categories.indices.contains(i) ? categories[i] : ""
    }
    return xPercent ? Format.percent(value, fraction: true) : value.formatted(.number.grouping(.never).precision(.fractionLength(0...2)))
  }
}

struct ResearchChart: View {
  let artifact: JSONValue
  private let plot: ResearchPlot
  @State private var selectedX: Double?
  init(artifact: JSONValue) {
    self.artifact = artifact
    plot = ResearchPlot(artifact)
  }
  private var xTicks: [Double] {
    let count = plot.categories.count
    guard count > 0 else { return [] }
    let step = max(1, Int(ceil(Double(count) / 5)))
    return stride(from: 0, to: count, by: step).map(Double.init)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(artifact["title"].string).font(.subheadline.weight(.semibold))
      if !plot.matrix.isEmpty {
        ResearchMatrix(rows: plot.matrix, percent: plot.yPercent)
      } else if !plot.series.isEmpty {
        cartesian
        if let selectedX {
          VStack(alignment: .leading, spacing: 5) {
            ForEach(plot.series) { series in
              if let point = series.points.min(by: { abs($0.x - selectedX) < abs($1.x - selectedX) }) {
                HStack {
                  Text("\(plot.xLabel(point.x)) · \(series.name)")
                  Spacer()
                  if series.kind == "band" {
                    Text("\(value(point.lower)) – \(value(point.upper))")
                  } else { Text(value(point.y)) }
                }
              }
            }
          }.font(.caption2).monospacedDigit()
        }
      }
    }.padding(.vertical, 12)
  }

  private func value(_ number: Double?) -> String {
    plot.yPercent ? Format.percent(number, fraction: true) : Format.number(number)
  }
  private var cartesian: some View {
    Chart {
      ForEach(plot.series) { series in
        ForEach(series.points) { point in
          if series.kind == "band", let lower = point.lower, let upper = point.upper {
            AreaMark(x: .value(plot.xTitle, point.x), yStart: .value("Lower", lower),
              yEnd: .value("Upper", upper), series: .value("Segment", point.segment))
              .foregroundStyle(by: .value("Series", series.name)).opacity(0.2)
          } else if let y = point.y {
            if series.kind == "candlestick", let open = point.open, let low = point.lower, let high = point.upper {
              RuleMark(x: .value(plot.xTitle, point.x), yStart: .value("Low", low), yEnd: .value("High", high))
                .foregroundStyle(Theme.tone(y - open))
              BarMark(x: .value(plot.xTitle, point.x), yStart: .value("Open", min(open, y)),
                yEnd: .value("Close", max(open, y)), width: .fixed(5))
                .foregroundStyle(Theme.tone(y - open))
            } else if series.kind == "bar" {
              if let left = point.row.number(series.left), let right = point.row.number(series.right) {
                RectangleMark(xStart: .value("Bin start", left), xEnd: .value("Bin end", right),
                  yStart: .value("Baseline", 0), yEnd: .value(plot.yTitle, y))
                  .foregroundStyle(by: .value("Series", series.name))
              } else {
                BarMark(x: .value(plot.xTitle, point.x), y: .value(plot.yTitle, y))
                  .foregroundStyle(by: .value("Series", series.name)).position(by: .value("Series", series.name))
              }
            } else if series.kind == "scatter" {
              PointMark(x: .value(plot.xTitle, point.x), y: .value(plot.yTitle, y))
                .foregroundStyle(by: .value("Series", series.name))
            } else {
              LineMark(x: .value(plot.xTitle, point.x), y: .value(plot.yTitle, y),
                series: .value("Segment", point.segment))
                .foregroundStyle(by: .value("Series", series.name))
            }
          }
        }
      }
    }.frame(height: 270)
      .chartXSelection(value: $selectedX)
      .chartXScale(domain: .automatic(includesZero: false))
      .chartXAxisLabel(plot.xTitle).chartYAxisLabel(plot.yTitle)
      .chartXAxis {
        if plot.xType == "category" {
          AxisMarks(values: xTicks) { tick in
            AxisGridLine(); AxisTick()
            AxisValueLabel { if let x = tick.as(Double.self) { Text(plot.xLabel(x, axis: true)) } }
          }
        } else {
          AxisMarks(values: .automatic(desiredCount: 4)) { tick in
            AxisGridLine(); AxisTick()
            AxisValueLabel { if let x = tick.as(Double.self) { Text(plot.xLabel(x, axis: true)) } }
          }
        }
      }
      .chartYAxis {
        AxisMarks(position: .leading, values: .automatic(desiredCount: 5)) { tick in
          AxisGridLine(); AxisTick()
          AxisValueLabel { if let y = tick.as(Double.self) { Text(value(y)) } }
        }
      }
      .chartYScale(domain: .automatic(includesZero: plot.series.contains { $0.kind == "bar" }))
      .chartLegend(position: .bottom)
      .chartForegroundStyleScale(range: [Theme.teal, Theme.blue, Theme.violet, Theme.coral])
  }
}

private struct ResearchMatrix: View {
  let rows: [Record]
  let percent: Bool
  @State private var selected = ""
  private var columns: [String] { ordered(rows.map { $0.text("x") }) }
  private var labels: [String] { ordered(rows.map { $0.text("y") }) }
  private func ordered(_ values: [String]) -> [String] {
    var seen = Set<String>()
    return values.filter { seen.insert($0).inserted }
  }
  var body: some View {
    let cells = Dictionary(uniqueKeysWithValues: rows.map { ([$0.text("x"), $0.text("y")], $0.number("z")) })
    let extent = max(rows.compactMap { $0.number("z") }.map(abs).max() ?? 1, 0.000001)
    VStack(alignment: .leading, spacing: 8) {
      if !selected.isEmpty { Text(selected).font(.caption).monospacedDigit() }
      ScrollView([.horizontal, .vertical]) {
        VStack(alignment: .leading, spacing: 3) {
          HStack(spacing: 3) {
            Color.clear.frame(width: 90, height: 34)
            ForEach(columns, id: \.self) { Text($0).font(.caption2).frame(width: 68, height: 34) }
          }
          ForEach(labels, id: \.self) { row in
            HStack(spacing: 3) {
              Text(row).font(.caption2).frame(width: 90, alignment: .leading)
              ForEach(columns, id: \.self) { column in
                let number = cells[[column, row]] ?? nil
                let text = percent ? Format.percent(number, fraction: true) : Format.number(number)
                Button { selected = "\(row) · \(column): \(text)" } label: {
                  Text(text).font(.system(size: 11, design: .monospaced)).frame(width: 68, height: 36)
                    .background(number.map { Theme.tone($0).opacity(0.1 + 0.65 * abs($0) / extent) }
                      ?? Color.white.opacity(0.03), in: RoundedRectangle(cornerRadius: 3))
                }.buttonStyle(.plain).accessibilityLabel("\(row), \(column): \(text)")
              }
            }
          }
        }
      }.frame(height: min(380, CGFloat(labels.count * 39 + 40)))
    }
  }
}
