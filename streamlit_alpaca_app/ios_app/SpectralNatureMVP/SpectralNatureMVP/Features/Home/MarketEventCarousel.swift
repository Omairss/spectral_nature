import SwiftUI

struct MarketEventCalendar: Codable {
  struct Event: Codable, Identifiable {
    let id: String
    let name: String
    let ticker: String?
    let shortLabel: String?
    let type: String
    let time: String?
    let importance: Double
    let summary: String?
    let context: String?
    let reaction: String?
    let facts: [[String]]?

    var label: String { ticker ?? shortLabel ?? name }
  }

  struct Day: Codable, Identifiable {
    let id: String
    let label: String
    let events: [Event]

    var highlights: [Event] {
      let important = Set(events.sorted { $0.importance > $1.importance }.prefix(2).map(\.id))
      return events.filter { important.contains($0.id) }
    }
  }

  let referenceDate: String
  let days: [Day]
}

/// The horizontal lens stays in place as the event stacks unfold beneath each date.
struct MarketEventCarousel: View {
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  let calendar: MarketEventCalendar
  @State private var position: String?
  @State private var expanded = false
  @State private var selection: Selection?

  private struct Selection: Equatable {
    let day: String
    let event: String
  }

  private struct Centers: PreferenceKey {
    static var defaultValue: [String: CGFloat] = [:]
    static func reduce(value: inout [String: CGFloat], nextValue: () -> [String: CGFloat]) {
      value.merge(nextValue(), uniquingKeysWith: { _, next in next })
    }
  }

  private struct Snap: ScrollTargetBehavior {
    let step: CGFloat
    func updateTarget(_ target: inout ScrollTarget, context: TargetContext) {
      let limit = max(0, context.contentSize.width - context.containerSize.width)
      target.rect.origin.x = min(limit, max(0, (target.rect.minX / step).rounded() * step))
    }
  }

  init(calendar: MarketEventCalendar) {
    self.calendar = calendar
    _position = State(initialValue: calendar.referenceDate)
  }

  private var selectedDay: MarketEventCalendar.Day? {
    calendar.days.first { $0.id == position }
  }

  private var selectedEvent: MarketEventCalendar.Event? {
    guard let selection, selection.day == position else { return nil }
    return selectedDay?.events.first { $0.id == selection.event }
  }

  private var height: CGFloat {
    expanded ? 44 + CGFloat(min(calendar.days.map { $0.events.count }.max() ?? 0, 7)) * 40 : 94
  }

  private var animation: Animation? {
    reduceMotion ? nil : .spring(response: 0.38, dampingFraction: 0.88)
  }

  var body: some View {
    VStack(spacing: 0) {
      GeometryReader { viewport in
        let viewportWidth = viewport.size.width
        let step = viewportWidth / 5
        ScrollViewReader { reader in
          ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 0) {
              ForEach(calendar.days) { day in
                column(day, width: step) { id in reader.scrollTo(id, anchor: .center) }
                  .frame(width: step, height: height, alignment: .top)
                  .background {
                    GeometryReader { geometry in
                      Color.clear.preference(key: Centers.self,
                        value: [day.id: geometry.frame(in: .named("marketCalendarViewport")).midX])
                    }
                  }
                  .visualEffect { content, geometry in
                    let center: CGFloat = geometry.frame(in: .named("marketCalendarViewport")).midX
                    let depth: CGFloat = abs(center - viewportWidth / 2) / step
                    let scale: CGFloat = max(0.5, 1 - depth * 0.18)
                    let opacity: Double = Double(max(0.08, 1 - depth * 0.38))
                    return content.scaleEffect(scale, anchor: .top).opacity(opacity)
                  }
                  .id(day.id)
              }
            }
            .padding(.horizontal, step * 2)
          }
          .coordinateSpace(name: "marketCalendarViewport")
          .scrollIndicators(.hidden)
          .scrollTargetBehavior(Snap(step: step))
          .onAppear { reader.scrollTo(calendar.referenceDate, anchor: .center) }
          .onPreferenceChange(Centers.self) { centers in
            if let nearest = centers.min(by: {
              abs($0.value - viewportWidth / 2) < abs($1.value - viewportWidth / 2)
            })?.key { position = nearest }
          }
          .mask {
            LinearGradient(stops: [
              .init(color: .clear, location: 0),
              .init(color: .black.opacity(0.65), location: 0.09),
              .init(color: .black, location: 0.32),
              .init(color: .black, location: 0.68),
              .init(color: .black.opacity(0.65), location: 0.91),
              .init(color: .clear, location: 1),
            ], startPoint: .leading, endPoint: .trailing)
          }
        }
      }
      .frame(height: height)

      if expanded {
        HStack(spacing: 10) {
          if let day = selectedDay {
            Text("\(day.label) · \(day.events.count) \(day.events.count == 1 ? "event" : "events")")
              .font(.caption).foregroundStyle(Theme.secondaryText)
          }
          Spacer()
          Button {
            withAnimation(animation) { expanded = false; selection = nil }
          } label: {
            Image(systemName: "chevron.up").font(.system(size: 12, weight: .medium))
              .frame(width: 44, height: 44)
          }
          .buttonStyle(.plain)
          .accessibilityLabel("Collapse market events")
        }
        .padding(.horizontal, 20)
        .transition(.opacity)

        if let event = selectedEvent {
          eventDetail(event)
            .padding(.horizontal, 20)
            .padding(.bottom, 16)
            .transition(.opacity.combined(with: .move(edge: .top)))
        }
      }
    }
    .foregroundStyle(Theme.text)
    .animation(animation, value: expanded)
    .animation(animation, value: selection)
    .onChange(of: calendar.days.map(\.id)) { _, ids in
      if !ids.contains(position ?? "") { position = calendar.referenceDate; selection = nil }
    }
    .onChange(of: position) { _, next in
      if selection?.day != next { selection = nil }
    }
    .accessibilityIdentifier("market-event-carousel")
  }

  private func column(_ day: MarketEventCalendar.Day, width: CGFloat,
                      scrollTo: @escaping (String) -> Void) -> some View {
    // Stable column identity keeps the horizontal target alive while its rows expand.
    VStack(spacing: 0) {
      Button {
        withAnimation(animation) { scrollTo(day.id); expanded = true; selection = nil }
      } label: {
        dateLabel(day).frame(width: width, height: 44)
      }
      .buttonStyle(.plain)
      .accessibilityLabel("\(day.label), \(day.events.count) \(day.events.count == 1 ? "event" : "events")")
      .accessibilityHint(expanded ? "Centers this date" : "Expands the calendar in place")
      .accessibilityIdentifier("calendar-day-\(day.id)")

      ScrollView(.vertical) {
        VStack(spacing: 0) {
          ForEach(expanded ? day.events : day.highlights) { event in
            Button {
              if expanded {
                scrollTo(day.id)
                position = day.id
                withAnimation(animation) {
                  let next = Selection(day: day.id, event: event.id)
                  selection = selection == next ? nil : next
                }
              } else {
                withAnimation(animation) { scrollTo(day.id); expanded = true; selection = nil }
              }
            } label: {
              Text(event.label)
                .font(.system(size: 12 + CGFloat(min(1, max(0, event.importance))) * 2,
                              weight: selection?.event == event.id ? .semibold : .medium))
                .foregroundStyle(selection?.event == event.id ? Theme.teal : Theme.text)
                .lineLimit(1).minimumScaleFactor(0.7)
                .frame(width: width, height: expanded ? 40 : 22)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(event.name), \(event.time.map { $0 + " ET" } ?? "time unconfirmed")")
            .accessibilityIdentifier("calendar-event-\(event.id)")
          }
        }
      }
      .scrollDisabled(!expanded)
      .scrollIndicators(.hidden)
    }
  }

  private func dateLabel(_ day: MarketEventCalendar.Day) -> some View {
    Text(Format.date(day.id).map { Format.historyDate($0, years: false) } ?? day.label)
      .font(.system(size: 12, weight: day.id == position ? .semibold : .regular))
      .foregroundStyle(day.id == position ? Theme.text : Theme.secondaryText)
      .lineLimit(1).minimumScaleFactor(0.75)
      .frame(maxWidth: .infinity)
  }

  private func eventDetail(_ event: MarketEventCalendar.Event) -> some View {
    VStack(alignment: .leading, spacing: 10) {
      Divider().overlay(Theme.border)
      HStack(alignment: .firstTextBaseline) {
        Text(event.name).font(.subheadline.weight(.semibold))
        Spacer()
        if let time = event.time {
          Text(time + " ET").font(.caption.monospacedDigit()).foregroundStyle(Theme.secondaryText)
        }
      }
      if let summary = event.summary, !summary.isEmpty {
        Text(summary).font(.subheadline)
      }
      if let context = event.context, !context.isEmpty {
        Text(context).font(.footnote).foregroundStyle(Theme.secondaryText).fixedSize(horizontal: false, vertical: true)
      }
      if let facts = event.facts, !facts.isEmpty {
        ForEach(Array(facts.enumerated()), id: \.offset) { _, fact in
          if fact.count >= 2 {
            HStack(alignment: .firstTextBaseline) {
              Text(fact[0]).foregroundStyle(Theme.secondaryText)
              Spacer()
              Text(fact[1]).multilineTextAlignment(.trailing)
            }.font(.caption)
          }
        }
      }
      if let reaction = event.reaction, !reaction.isEmpty {
        Text(reaction).font(.caption).foregroundStyle(Theme.secondaryText)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityIdentifier("calendar-event-detail")
  }
}
