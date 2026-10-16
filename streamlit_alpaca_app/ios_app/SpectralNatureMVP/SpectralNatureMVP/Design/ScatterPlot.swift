import Charts
import SwiftUI

/// One numeric scale owns both the marks and their visible legend.
struct PlotColorScale: Sendable {
  let domain: ClosedRange<Double>
  let centered: Bool
  let hasValues: Bool
  let clippedBelow: Bool
  let clippedAbove: Bool

  init(values: [Double], domain: ClosedRange<Double>? = nil, centered: Bool = false, clipOutliers: Bool = false) {
    hasValues = !values.isEmpty
    let low = domain?.lowerBound ?? values.min() ?? 0
    let high = domain?.upperBound ?? values.max() ?? 0
    self.centered = centered || (domain == nil && low < 0 && high > 0)
    if self.centered {
      let magnitudes = values.map(abs).sorted()
      let fullExtent = max(abs(low), abs(high))
      // Keep typical daily moves distinguishable. Saturated tails are explicitly
      // labeled with inequalities; inspection always retains the original value.
      let percentile = magnitudes.isEmpty ? fullExtent : magnitudes[Int((Double(magnitudes.count - 1) * 0.95).rounded(.up))]
      let extent = clipOutliers && domain == nil && percentile > 0 ? percentile : fullExtent
      self.domain = -extent...extent
    } else {
      self.domain = low...high
    }
    clippedBelow = (values.min() ?? low) < self.domain.lowerBound
    clippedAbove = (values.max() ?? high) > self.domain.upperBound
  }

  func color(_ value: Double?) -> Color {
    guard let value else { return Color.gray }
    let span = domain.upperBound - domain.lowerBound
    guard span > 0 else { return centered ? .gray : Theme.blue }
    let p = min(1, max(0, (value - domain.lowerBound) / span))
    if centered {
      // Zero is neutral. Equal positive/negative magnitudes have equal saturation.
      let strength = abs(p - 0.5) * 2
      let end = p < 0.5 ? (0.94, 0.55, 0.45) : (0.32, 0.84, 0.78)
      return Color(
        red: 0.58 + strength * (end.0 - 0.58),
        green: 0.62 + strength * (end.1 - 0.62),
        blue: 0.65 + strength * (end.2 - 0.65))
    }
    return Color(red: 0.70 - p * 0.38, green: 0.62 + p * 0.22, blue: 0.96 - p * 0.18)
  }
}

struct PlotColorLegend: View {
  let title: String
  let scale: PlotColorScale
  var percent = false
  var fraction = false
  var hasMissing = false
  private func value(_ number: Double) -> String {
    percent || fraction ? Format.percent(number, fraction: fraction) : Format.number(number)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 5) {
      HStack {
        Text("Color: \(title)")
        Spacer()
        if hasMissing {
          Circle().fill(.gray).frame(width: 6, height: 6)
          Text("Unavailable")
        }
      }
      if !scale.hasValues {
        EmptyView()
      } else if scale.domain.lowerBound != scale.domain.upperBound {
        LinearGradient(
          colors: [scale.color(scale.domain.lowerBound),
                   scale.color((scale.domain.lowerBound + scale.domain.upperBound) / 2),
                   scale.color(scale.domain.upperBound)],
          startPoint: .leading, endPoint: .trailing
        ).frame(height: 5).clipShape(Capsule()).accessibilityHidden(true)
        HStack {
          Text((scale.clippedBelow ? "≤ " : "") + value(scale.domain.lowerBound))
          Spacer()
          if scale.centered { Text("0"); Spacer() }
          Text((scale.clippedAbove ? "≥ " : "") + value(scale.domain.upperBound))
        }.monospacedDigit()
      } else {
        HStack {
          Circle().fill(scale.color(scale.domain.lowerBound)).frame(width: 7, height: 7)
          Text(value(scale.domain.lowerBound))
        }
      }
    }.font(.caption2).foregroundStyle(.secondary)
  }
}

struct ScatterPlot: View {
  let rows: [Record]
  let x: String
  let y: String
  let label: String
  let xTitle: String
  let yTitle: String
  var color: String? = nil
  var size: String? = nil
  var sizeTitle: String? = nil
  var logarithmicSize = false
  var movement: String? = nil
  var scale = 1.0
  var colorDomain: ClosedRange<Double>? = nil
  var colorAsFraction = false
  var colorTitle: String? = nil
  var colorAsPercent = false
  var colorCenteredAtZero = false
  var colorClipOutliers = false
  var percentAxes = false
  var includeZero = false
  var labelExtremes = false
  var showSelectionDetails = true
  var onSelect: ((Record) -> Void)? = nil
  var identity: String? = nil
  var pointLimit: Int? = nil

  @State private var selectedID: String?
  @State private var inspecting = false
  @State private var search = ""
  @State private var prepared: Prepared?

  private struct Point: Identifiable, Sendable {
    let id: Int
    let key: String
    let name: String
    let row: Record
    let x: Double
    let y: Double
  }
  private struct Input: Equatable, Sendable {
    let rows: [Record]
    let x: String
    let y: String
    let label: String
    let identity: String?
    let color: String?
    let size: String?
    let scale: Double
    let colorDomain: ClosedRange<Double>?
    let centered: Bool
    let clipOutliers: Bool
    let includeZero: Bool
    let pointLimit: Int?
  }
  private struct Prepared: Sendable {
    let plotted: [Point]
    let colors: PlotColorScale
    let xDomain: ClosedRange<Double>
    let yDomain: ClosedRange<Double>
    let sizeValues: [Double]
    let extremes: Set<Int>
  }
  nonisolated private static func prepare(_ input: Input) -> Prepared {
    let complete = input.rows.enumerated().compactMap { index, row -> Point? in
      guard let x = row.number(input.x), let y = row.number(input.y), x.isFinite, y.isFinite else { return nil }
      let name = row.text(input.label)
      let key = row.text(input.identity ?? input.label)
      return Point(id: index, key: key.isEmpty ? "\(index):\(name)" : key,
        name: name, row: row, x: x * input.scale, y: y * input.scale)
    }
    let plotted = input.pointLimit.map { limit in
      PeripheralScatterSelection.indices(complete.map { .init(x: $0.x, y: $0.y) }, limit: limit).map { complete[$0] }
    } ?? complete
    func bounds(_ values: [Double]) -> ClosedRange<Double> {
      let low = min(values.min() ?? 0, input.includeZero ? 0 : .infinity)
      let high = max(values.max() ?? 0, input.includeZero ? 0 : -.infinity)
      let padding = high > low ? (high - low) * 0.08 : max(abs(low) * 0.08, 1)
      return (low - padding)...(high + padding)
    }
    return Prepared(plotted: plotted,
      colors: PlotColorScale(values: complete.compactMap { point in input.color.flatMap { point.row.number($0) } },
        domain: input.colorDomain, centered: input.centered, clipOutliers: input.clipOutliers),
      xDomain: bounds(complete.map(\.x)), yDomain: bounds(complete.map(\.y)),
      sizeValues: complete.compactMap { point in input.size.flatMap { point.row.number($0) } }.filter { $0.isFinite && $0 >= 0 }.sorted(),
      extremes: Set([plotted.min(by: { $0.x < $1.x })?.id, plotted.max(by: { $0.x < $1.x })?.id,
        plotted.min(by: { $0.y < $1.y })?.id, plotted.max(by: { $0.y < $1.y })?.id].compactMap { $0 }))
  }
  private func axisValue(_ value: Double?) -> String {
    percentAxes ? Format.percent(value) : Format.number(value)
  }
  private func select(_ point: Point) {
    selectedID = point.key
    onSelect?(point.row)
  }
  private func title(_ key: String) -> String {
    key.replacingOccurrences(of: "_", with: " ").capitalized
  }
  private func area(_ value: Double?, values: [Double]) -> Double {
    guard size != nil, let value, value >= 0, let low = values.first, let high = values.last else { return 36 }
    guard high > low else { return 72 }
    let position = logarithmicSize ? (log1p(value) - log1p(low)) / (log1p(high) - log1p(low)) : (value - low) / (high - low)
    return 28 + 140 * min(1, max(0, position))
  }
  private func volumeLabel(_ value: Double) -> String {
    value.formatted(.number.notation(.compactName).precision(.fractionLength(0...1)))
  }

  var body: some View {
    let input = Input(rows: rows, x: x, y: y, label: label, identity: identity, color: color, size: size,
      scale: scale, colorDomain: colorDomain, centered: colorCenteredAtZero, clipOutliers: colorClipOutliers,
      includeZero: includeZero, pointLimit: pointLimit)
    let plotted = prepared?.plotted ?? []
    let selected = plotted.first { $0.key == selectedID }
    VStack(alignment: .leading, spacing: 12) {
      if prepared == nil {
        ProgressView().frame(maxWidth: .infinity).frame(height: 280)
      } else if plotted.isEmpty {
        Text("No paired values for these axes").font(.caption).foregroundStyle(.secondary)
      } else {
        plot(plotted, prepared: prepared!)
        if let color {
          PlotColorLegend(
            title: colorTitle ?? title(color), scale: prepared!.colors, percent: colorAsPercent,
            fraction: colorAsFraction, hasMissing: plotted.contains { $0.row.number(color) == nil })
        }
        if movement != nil {
          HStack(spacing: 16) {
            Text("Shape: 1D")
            ForEach([MovementMark.Kind.up, .down, .flat], id: \.self) { kind in
              HStack(spacing: 4) {
                MovementMark(kind: kind).fill(Theme.text).frame(width: 9, height: 9)
                Text(kind.rawValue)
              }
            }
          }.font(.caption2).foregroundStyle(.secondary)
          if let movement, plotted.contains(where: { $0.row.number(movement) == nil }) {
            HStack(spacing: 4) {
              MovementMark(kind: .unavailable).fill(Theme.text).frame(width: 9, height: 9)
              Text("1D unavailable")
            }.font(.caption2).foregroundStyle(.secondary)
          }
        }
        if let size, let values = prepared?.sizeValues, !values.isEmpty {
          VStack(alignment: .leading, spacing: 6) {
            Text("Size: \(sizeTitle ?? title(size))\(logarithmicSize ? " (log)" : "")")
            HStack {
            ForEach(Array(Set([values.first!, values[values.count / 2], values.last!])).sorted(), id: \.self) { value in
              HStack(spacing: 3) {
                Circle().stroke(Theme.secondaryText).frame(width: sqrt(area(value, values: values)), height: sqrt(area(value, values: values)))
                Text(volumeLabel(value))
              }
              if value != values.last { Spacer() }
            }
            }
          }.font(.caption2).foregroundStyle(.secondary)
        }
        if let size, plotted.contains(where: { point in
          guard let value = point.row.number(size) else { return true }
          return !value.isFinite || value < 0
        }) {
          Text("\(sizeTitle ?? title(size)) unavailable: fixed marker size")
            .font(.caption2).foregroundStyle(.secondary)
        }
        Button { inspecting = true } label: {
          HStack {
            Text(selected?.name ?? "Inspect a point")
            Spacer()
            Image(systemName: "magnifyingglass")
          }.font(.subheadline)
        }.buttonStyle(.bordered)
        if showSelectionDetails, let selected {
          Metrics(items: [(xTitle, axisValue(selected.x)), (yTitle, axisValue(selected.y))])
          if let color {
            Text("\(colorTitle ?? title(color)): \(colorAsPercent || colorAsFraction ? Format.percent(selected.row.number(color), fraction: colorAsFraction) : Format.number(selected.row.number(color)))")
              .font(.caption).monospacedDigit()
          }
          if let size {
            Text("\(sizeTitle ?? title(size)): \(Format.number(selected.row.number(size), digits: 0))")
              .font(.caption).monospacedDigit()
          }
        }
      }
    }.task(id: input) {
      let task = Task.detached(priority: .userInitiated) { Self.prepare(input) }
      let next = await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
      guard !Task.isCancelled else { return }
      prepared = next
      if !next.plotted.contains(where: { $0.key == selectedID }) { selectedID = nil }
    }.sheet(isPresented: $inspecting) {
      NavigationStack {
        List {
          ForEach(plotted.filter {
            search.isEmpty || $0.name.localizedCaseInsensitiveContains(search)
              || $0.row.text("company_name").localizedCaseInsensitiveContains(search)
          }.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }) { point in
            Button {
              select(point)
              inspecting = false
            } label: {
              VStack(alignment: .leading, spacing: 5) {
                Text(point.name).font(.subheadline.weight(.semibold))
                if !point.row.text("company_name").isEmpty {
                  Text(point.row.text("company_name")).font(.caption).foregroundStyle(.secondary)
                }
                Text("\(xTitle) \(axisValue(point.x)) · \(yTitle) \(axisValue(point.y))")
                  .font(.caption2).foregroundStyle(.secondary)
              }
            }.buttonStyle(.plain)
          }
        }.searchable(text: $search, prompt: "Name or symbol").navigationTitle("Inspect a point")
          .navigationBarTitleDisplayMode(.inline)
          .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { inspecting = false } } }
          .scrollContentBackground(.hidden).workbench()
      }.preferredColorScheme(.dark)
    }
  }

  private func plot(_ points: [Point], prepared: Prepared) -> some View {
    Chart {
      if includeZero {
        RuleMark(x: .value(xTitle, 0)).foregroundStyle(.white.opacity(0.25))
          .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3])).accessibilityHidden(true)
        RuleMark(y: .value(yTitle, 0)).foregroundStyle(.white.opacity(0.25))
          .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3])).accessibilityHidden(true)
      }
      ForEach(points) { point in
        PointMark(x: .value(xTitle, point.x), y: .value(yTitle, point.y))
          .foregroundStyle(color == nil ? Theme.teal : prepared.colors.color(color.flatMap { point.row.number($0) }))
          .symbol(MovementMark(kind: movement.map { MovementMark.Kind(value: point.row.number($0)) } ?? .flat))
          .symbolSize(area(size.flatMap { point.row.number($0) }, values: prepared.sizeValues))
          .opacity(selectedID == nil || point.key == selectedID ? 0.9 : 0.5)
          .annotation(position: .top, spacing: 4,
            overflowResolution: .init(x: .fit(to: .chart), y: .fit(to: .chart))) {
            if point.key == selectedID || (selectedID == nil && labelExtremes && prepared.extremes.contains(point.id)) {
              Text(point.name).font(.system(size: 10, weight: .semibold))
                .padding(.horizontal, 3).padding(.vertical, 2)
                .background(Theme.panel.opacity(0.92), in: RoundedRectangle(cornerRadius: 3))
            }
          }
          .accessibilityLabel(point.name)
          .accessibilityValue("\(xTitle) \(axisValue(point.x)), \(yTitle) \(axisValue(point.y))")
        if point.key == selectedID {
          PointMark(x: .value(xTitle, point.x), y: .value(yTitle, point.y))
            .symbolSize(area(size.flatMap { point.row.number($0) }, values: prepared.sizeValues) + 90)
            .symbol { Circle().stroke(.white, lineWidth: 2).frame(width: 16, height: 16) }
            .accessibilityHidden(true)
        }
      }
    }
    .chartXAxisLabel(xTitle).chartYAxisLabel(yTitle)
    .chartXScale(domain: prepared.xDomain)
    .chartYScale(domain: prepared.yDomain)
    .chartXAxis {
      AxisMarks(values: .automatic(desiredCount: 4)) { value in
        AxisGridLine(); AxisTick()
        AxisValueLabel(anchor: .top) {
          if let number = value.as(Double.self) {
            Text(axisValue(number)).font(.system(size: 10)).fixedSize()
          }
        }
      }
    }
    .chartYAxis {
      AxisMarks(values: .automatic(desiredCount: 4)) { value in
        AxisGridLine(); AxisTick()
        AxisValueLabel {
          if let number = value.as(Double.self) {
            Text(axisValue(number)).font(.system(size: 10)).fixedSize()
          }
        }
      }
    }
    .chartOverlay { proxy in
      GeometryReader { geometry in
        Color.clear.contentShape(Rectangle())
          .onTapGesture { location in
            guard let anchor = proxy.plotFrame else { return }
            let frame = geometry[anchor]
            guard frame.contains(location) else { return }
            let tap = CGPoint(x: location.x - frame.minX, y: location.y - frame.minY)
            // Distances must be measured in screen coordinates: x-only selection
            // chooses the wrong instrument in vertically stacked/dense clouds.
            let closest = points.compactMap { point -> (Point, CGFloat)? in
              guard let position = proxy.position(for: (point.x, point.y)) else { return nil }
              return (point, hypot(position.x - tap.x, position.y - tap.y))
            }.min { $0.1 < $1.1 }
            if let closest { select(closest.0) }
          }.accessibilityHidden(true)
      }
    }.frame(height: 280)
  }
}


private struct MovementMark: ChartSymbolShape {
  enum Kind: String, Hashable { case up = "Up", down = "Down", flat = "Flat", unavailable = "Unavailable"
    init(value: Double?) {
      guard let value else { self = .unavailable; return }
      self = value > 0 ? .up : value < 0 ? .down : .flat
    }
  }
  let kind: Kind
  var perceptualUnitRect: CGRect { CGRect(x: 0, y: 0, width: 1, height: 1) }
  func path(in rect: CGRect) -> Path {
    switch kind {
    case .flat: return Path(ellipseIn: rect)
    case .unavailable: return Path(rect)
    case .up, .down:
      var path = Path()
      path.move(to: CGPoint(x: rect.midX, y: kind == .up ? rect.minY : rect.maxY))
      path.addLine(to: CGPoint(x: rect.maxX, y: kind == .up ? rect.maxY : rect.minY))
      path.addLine(to: CGPoint(x: rect.minX, y: kind == .up ? rect.maxY : rect.minY))
      path.closeSubpath()
      return path
    }
  }
}
