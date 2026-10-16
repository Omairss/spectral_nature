import Charts
import SwiftUI

/// Daily closes and the expanded latest session share a price axis, without
/// connecting the two time scales or inventing observations between sessions.
struct PriceContextChart: View {
  let symbol: String
  let daily: [TrendPoint]
  let intraday: TickerSnapshot.IntradayHistory?
  @State private var selectedX: Double?

  private struct Point: Identifiable {
    let date: Date
    let value: Double
    let session: Bool
    var x = 0.0
    var id: String { "\(session):\(date.timeIntervalSince1970)" }
  }

  private var segments: (week: [Point], day: [Point]) {
    func observations(_ values: [TrendPoint], session: Bool) -> [Point] {
      values.compactMap { point in
        guard point.value.isFinite, let date = Format.date(point.date) else { return nil }
        return Point(date: date, value: point.value, session: session)
      }.sorted { $0.date < $1.date }
    }
    let history = observations(daily, session: false)
    let day = observations(intraday?.points ?? [], session: true)
    let end = day.first.flatMap { point in
      Format.date(intraday?.session ?? Format.observationDay(point.date))
    } ?? history.last?.date
    guard let end else { return ([], []) }
    let start = end.addingTimeInterval(-7 * 86_400)
    let week = history.filter { $0.date >= start && (day.isEmpty ? $0.date <= end : $0.date < end) }
    let split = !week.isEmpty && !day.isEmpty
    func positioned(_ values: [Point], lower: Double, upper: Double) -> [Point] {
      guard let first = values.first, let last = values.last else { return [] }
      let duration = last.date.timeIntervalSince(first.date)
      return values.map { point in
        var copy = point
        copy.x = duration > 0
          ? lower + point.date.timeIntervalSince(first.date) / duration * (upper - lower)
          : (lower + upper) / 2
        return copy
      }
    }
    return (
      positioned(week, lower: 0, upper: split ? 0.38 : 1),
      positioned(day, lower: split ? 0.48 : 0, upper: 1)
    )
  }

  private func dayLabel(_ date: Date) -> String {
    Format.historyDate(date, years: false)
  }

  var body: some View {
    let data = segments
    let points = data.week + data.day
    if let latest = points.last {
      let split = !data.week.isEmpty && !data.day.isEmpty
      let low = points.map(\.value).min() ?? latest.value
      let high = points.map(\.value).max() ?? latest.value
      let span = high > low ? high - low : max(abs(high) * 0.01, 0.01)
      let minimum = low - span * 0.08
      let maximum = high + span * 0.08
      let selected = selectedX.flatMap { x in points.min { abs($0.x - x) < abs($1.x - x) } }
      VStack(alignment: .leading, spacing: 6) {
        HStack {
          Text(selected.map {
            dayLabel($0.date) + ($0.session ? " · \(Format.marketTime($0.date)) ET" : " · close")
          } ?? (split ? "Week + latest day" : data.day.isEmpty ? "Past week" : "Latest day"))
            .foregroundStyle(.secondary)
          Spacer(minLength: 6)
          if let selected { Text(Format.price(selected.value)).monospacedDigit() }
        }.font(.caption2)
        Chart {
          if split {
            RectangleMark(
              xStart: .value("Session start", 0.48), xEnd: .value("Session end", 1.0),
              yStart: .value("Minimum", minimum), yEnd: .value("Maximum", maximum)
            ).foregroundStyle(.white.opacity(0.035))
            RuleMark(x: .value("Time scale boundary", 0.43))
              .foregroundStyle(.secondary.opacity(0.35))
              .lineStyle(StrokeStyle(lineWidth: 1, dash: [2, 4]))
          }
          ForEach(data.week) { point in
            LineMark(x: .value("Position", point.x), y: .value("Price", point.value),
                     series: .value("History", "Daily closes"))
              .foregroundStyle(Theme.teal.opacity(split ? 0.5 : 1))
              .lineStyle(StrokeStyle(lineWidth: 1.8))
            PointMark(x: .value("Position", point.x), y: .value("Price", point.value))
              .foregroundStyle(Theme.teal.opacity(split ? 0.6 : 1)).symbolSize(12)
          }
          ForEach(data.day) { point in
            LineMark(x: .value("Position", point.x), y: .value("Price", point.value),
                     series: .value("History", "Latest session"))
              .foregroundStyle(Theme.teal).lineStyle(StrokeStyle(lineWidth: 1.8))
          }
          if let selected {
            RuleMark(x: .value("Selected", selected.x)).foregroundStyle(.secondary.opacity(0.4))
          }
          PointMark(x: .value("Position", (selected ?? latest).x),
                    y: .value("Price", (selected ?? latest).value))
            .foregroundStyle(Theme.teal).symbolSize(25)
        }
        .chartXScale(domain: 0...1, range: .plotDimension(startPadding: 4, endPadding: 8))
        .chartYScale(domain: minimum...maximum)
        .chartXAxis(.hidden)
        .chartYAxis {
          AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { axis in
            AxisGridLine()
            AxisValueLabel {
              if let value = axis.as(Double.self) { Text(Format.price(value)).font(.system(size: 9)) }
            }
          }
        }
        .chartLegend(.hidden)
        .chartXSelection(value: $selectedX)
        .frame(height: 128)
        .accessibilityLabel("\(symbol) price history, \(data.week.count) daily closes and \(data.day.count) intraday observations")
        .accessibilityValue("\(dayLabel((selected ?? latest).date)), \(Format.marketTime((selected ?? latest).date)) ET, \(Format.price((selected ?? latest).value))")
        .accessibilityAdjustableAction { direction in
          let current = points.firstIndex { $0.id == (selected ?? latest).id } ?? points.count - 1
          switch direction {
          case .increment: selectedX = points[min(current + 1, points.count - 1)].x
          case .decrement: selectedX = points[max(current - 1, 0)].x
          @unknown default: break
          }
        }
        HStack(alignment: .top, spacing: 12) {
          if let first = data.week.first, let last = data.week.last {
            Text(first.date == last.date ? dayLabel(first.date) : "\(dayLabel(first.date)) – \(dayLabel(last.date))")
              .frame(maxWidth: .infinity, alignment: .leading)
          }
          if let first = data.day.first {
            Text("\(dayLabel(first.date)) · intraday").frame(maxWidth: .infinity, alignment: .trailing)
          }
        }.font(.system(size: 9)).foregroundStyle(.secondary)
      }
    }
  }
}

struct FundamentalHistoryChart: View {
  let label: String
  let value: Double?
  let history: [TrendPoint]
  let color: Color
  @State private var selectedDate: Date?
  private struct Point: Identifiable {
    let date: Date
    let value: Double
    let period: String?
    var id: Date { date }
  }
  private var points: [Point] {
    history.compactMap { point in
      guard point.value.isFinite, let date = Format.date(point.date) else { return nil }
      return Point(date: date, value: point.value, period: point.period)
    }.sorted { $0.date < $1.date }
  }
  private func period(_ point: Point) -> String {
    point.period.flatMap { $0.isEmpty ? nil : $0 } ?? Format.observationDay(point.date)
  }
  var body: some View {
    let data = points
    let selected = selectedDate.flatMap { date in
      data.min { abs($0.date.timeIntervalSince(date)) < abs($1.date.timeIntervalSince(date)) }
    }
    VStack(alignment: .leading, spacing: 5) {
      HStack(alignment: .top, spacing: 4) {
        Text(label).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        Spacer(minLength: 0)
        Text(Format.money(selected?.value ?? value ?? data.last?.value, compact: true))
          .fontWeight(.medium).monospacedDigit().lineLimit(1).minimumScaleFactor(0.75)
      }.font(.caption2).frame(minHeight: 26, alignment: .top)
      if let first = data.first, let last = data.last {
        Chart {
          ForEach(data) { point in
            LineMark(x: .value("Date", point.date), y: .value(label, point.value))
              .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 1.6))
          }
          PointMark(x: .value("Date", (selected ?? last).date),
                    y: .value(label, (selected ?? last).value))
            .foregroundStyle(color).symbolSize(12)
          if let selected {
            RuleMark(x: .value("Selected", selected.date)).foregroundStyle(.secondary.opacity(0.35))
          }
        }
        .chartXAxis(.hidden).chartYAxis(.hidden).chartLegend(.hidden)
        .chartYScale(domain: .automatic(includesZero: false))
        .chartXSelection(value: $selectedDate)
        .frame(height: 48)
        .accessibilityLabel("\(label), \(data.count) observations, \(period(first)) to \(period(last))")
        .accessibilityValue(Format.money(selected?.value ?? value ?? last.value))
        HStack(spacing: 4) {
          Text(selected.map(period) ?? period(first))
          Spacer(minLength: 0)
          if selected == nil { Text(period(last)) }
        }.font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.8)
          .frame(maxWidth: .infinity)
      }
    }.frame(maxWidth: .infinity, alignment: .leading).accessibilityElement(children: .contain)
  }
}
