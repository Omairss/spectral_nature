import AVFoundation
import SwiftUI

struct HomeView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<HomeSnapshot>()
  @State private var date = Date()
  @State private var replay = false
  @State private var showingDate = false
  @State private var expandedBrief = false
  @State private var section = "Brief"
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        HStack {
          Text(replay ? date.formatted(date: .abbreviated, time: .omitted) : "Daily Brief").font(
            .largeTitle.weight(.semibold))
          Spacer()
          if replay {
            Button("Latest") {
              replay = false
              Task { await load() }
            }.font(.caption)
          }
        }
        Picker("Daily Brief view", selection: $section) {
          Text("Brief").tag("Brief")
          Text("Attention").tag("Attention")
        }.pickerStyle(.segmented)
        ResourceStatus(resource: resource) { Task { await load() } }
        if let home = resource.value {
          if home.status != "available" {
            Text("Daily Brief unavailable").foregroundStyle(.secondary)
          } else {
            if section == "Brief" {
              if let summary = home.summary {
                VStack(alignment: .leading, spacing: 16) {
                  Text(summary.headline).font(.title2.weight(.medium))
                  if expandedBrief {
                    MarkdownView(text: summary.markdown)
                  } else {
                    Text(
                      (try? AttributedString(
                        markdown: summary.markdown,
                        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
                        ?? AttributedString(summary.markdown)
                    )
                    .font(.subheadline).lineSpacing(4).lineLimit(5)
                  }
                  Button(expandedBrief ? "Collapse brief" : "Read brief") {
                    withAnimation { expandedBrief.toggle() }
                  }.font(.subheadline)
                  if summary.audioAvailable { BriefAudio(date: replay ? Format.day(date) : "") }
                }
              }
              if let points = home.dailyDatapoints, !points.isEmpty {
                Text("Key datapoints").font(.title3.weight(.semibold)).padding(.top, 12)
                ScrollView(.horizontal) {
                  HStack(alignment: .top, spacing: 16) {
                    ForEach(points) { point in
                      Panel(point.title) {
                        TimeSeriesChart(trend: point.points, label: point.label)
                        Text(point.significance).font(.subheadline).foregroundStyle(.secondary)
                        Text("\(point.label) · \(point.unit) · \(point.date)").font(.caption2)
                          .foregroundStyle(.secondary)
                      }.containerRelativeFrame(.horizontal)
                    }
                  }.scrollTargetLayout()
                }.scrollTargetBehavior(.viewAligned).scrollIndicators(.hidden)
              }
            } else {
              Text("\(home.beats.count) signals").font(.caption.monospacedDigit()).foregroundStyle(
                .secondary)
              ForEach(home.beats) { beat in
                Panel {
                  Text(beat.kind == "unresolved" ? "Watch" : beat.kind.capitalized).font(
                    .caption.weight(.medium)
                  ).foregroundStyle(Theme.violet)
                  Text(beat.title).font(.headline)
                  if !beat.summary.isEmpty { MarkdownView(text: beat.summary) }
                  if !beat.businessContext.isEmpty {
                    DisclosureGroup("Business context") {
                      MarkdownView(text: beat.businessContext).padding(.top, 10)
                    }.font(.subheadline)
                  }
                  ForEach(beat.tickers) { ticker in
                    NavigationLink {
                      AccessGate { InvestigatorView(ticker: ticker.symbol) }
                    } label: {
                      TickerCard(ticker: ticker)
                    }.buttonStyle(.plain)
                  }
                }
              }
              if let graph = home.graph {
                NavigationLink {
                  AttentionGraphView(artifact: graph)
                } label: {
                  Label("Attention network", systemImage: "point.3.connected.trianglepath.dotted")
                    .frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(.bordered)
              }
            }
            if let asOf = Format.date(home.asOf) {
              Text("Updated \(asOf.formatted(date: .abbreviated, time: .shortened))").font(
                .caption2
              ).foregroundStyle(.secondary)
            }
          }
        }
      }.padding(20)
    }
    .navigationBarTitleDisplayMode(.inline)
    .toolbar {
      ToolbarItem(placement: .principal) { Brand() }
      ToolbarItem(placement: .topBarLeading) {
        Button {
          showingDate = true
        } label: {
          Image(systemName: "calendar")
        }.accessibilityLabel("Replay Daily Brief")
      }
    }
    .workbench()
    .researchContext(
      "Daily Brief; date=\(replay ? Format.day(date) : "latest"); as_of=\(resource.value?.asOf ?? "")"
    )
    .task { await load() }
    .refreshable { await load() }
    .sheet(isPresented: $showingDate) {
      NavigationStack {
        DatePicker("Snapshot date", selection: $date, in: ...Date(), displayedComponents: .date)
          .datePickerStyle(.graphical).padding()
          .navigationTitle("Replay").navigationBarTitleDisplayMode(.inline)
          .toolbar {
            ToolbarItem(placement: .confirmationAction) {
              Button("View") {
                replay = true
                showingDate = false
                Task { await load() }
              }
            }
          }
      }.presentationDetents([.medium, .large])
    }
  }
  private func load() async {
    let target = replay ? Format.day(date) : ""
    await resource.load(key: target) {
      try await session.api.get("/api/home", query: target.isEmpty ? [:] : ["date": target])
    }
  }
}

private struct TickerCard: View {
  let ticker: TickerSnapshot
  var body: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 4) {
          Text(ticker.symbol).font(.subheadline.weight(.semibold))
          Text(ticker.companyName).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
        Spacer()
        VStack(alignment: .trailing, spacing: 4) {
          Text(Format.money(ticker.latestClose)).font(.subheadline.weight(.medium))
            .monospacedDigit()
          Text(Format.percent(ticker.priceChange1DPct)).font(.caption).monospacedDigit()
            .foregroundStyle(Theme.tone(ticker.priceChange1DPct))
        }
        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.secondary).padding(
          .top, 5)
      }
      Metrics(items: [
        ("Market cap", Format.money(ticker.marketCap, compact: true)),
        ("1 month", Format.percent(ticker.priceChange1MPct)),
        ("Revenue", Format.money(ticker.revenue, compact: true)),
        ("Revenue YoY", Format.percent(ticker.revenueYoyPct)),
        ("Net income", Format.money(ticker.netIncome, compact: true)),
        ("Free cash flow", Format.money(ticker.freeCashFlow, compact: true)),
      ])
      ForEach(
        [
          ("revenue", "Revenue"), ("net_income", "Net income"),
          ("free_cash_flow", "Free cash flow"),
        ], id: \.0
      ) { key, label in
        if let trend = ticker.financialTrends?[key], !trend.isEmpty {
          Text(label).font(.caption2).foregroundStyle(.secondary)
          TimeSeriesChart(trend: trend, label: label, height: 90)
        }
      }
      if !ticker.fundamentalsPeriod.isEmpty {
        Text(ticker.fundamentalsPeriod).font(.caption2).foregroundStyle(.secondary)
      }
    }.padding(14).background(Theme.background, in: RoundedRectangle(cornerRadius: 10))
  }
}

@MainActor
private final class BriefPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
  @Published var playing = false
  @Published var loading = false
  @Published var error = ""
  @Published var position = 0.0
  @Published var duration = 0.0
  @Published var rate: Float = 1
  private var generation = 0
  private var player: AVAudioPlayer?
  func updateClock() { position = player?.currentTime ?? 0 }
  func seek(_ value: Double) {
    player?.currentTime = value
    position = value
  }
  func setRate(_ value: Float) {
    rate = value
    player?.rate = value
  }
  func pause() {
    player?.pause()
    playing = false
  }
  func toggle(api: APIClient, date: String) async {
    if let player {
      if player.isPlaying {
        player.pause()
        playing = false
      } else {
        player.play()
        playing = true
      }
      return
    }
    generation += 1
    let request = generation
    loading = true
    error = ""
    defer { if request == generation { loading = false } }
    do {
      let bytes = try await api.data("/api/home/audio", query: date.isEmpty ? [:] : ["date": date])
      guard request == generation, !Task.isCancelled else { return }
      try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
      try AVAudioSession.sharedInstance().setActive(true)
      player = try AVAudioPlayer(data: bytes)
      player?.delegate = self
      player?.enableRate = true
      player?.rate = rate
      duration = player?.duration ?? 0
      player?.play()
      playing = true
    } catch { if request == generation { self.error = error.localizedDescription } }
  }
  func stop() {
    generation += 1
    loading = false
    position = 0
    duration = 0
    player?.stop()
    player = nil
    playing = false
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }
  nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
    Task { @MainActor in self.playing = false }
  }
}

private struct BriefAudio: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var player = BriefPlayer()
  let date: String
  var body: some View {
    VStack(alignment: .leading) {
      Button {
        Task { await player.toggle(api: session.api, date: date) }
      } label: {
        Label(
          player.playing ? "Pause brief" : "Listen to brief",
          systemImage: player.playing ? "pause.fill" : "play.fill")
      }.buttonStyle(.bordered).disabled(player.loading)
      if player.duration > 0 {
        Slider(
          value: Binding(get: { player.position }, set: { player.seek($0) }),
          in: 0...player.duration
        ).accessibilityLabel("Playback position")
        HStack {
          Text(clock(player.position) + " / " + clock(player.duration)).font(
            .caption.monospacedDigit()
          ).foregroundStyle(.secondary)
          Spacer()
          Picker("Speed", selection: Binding(get: { player.rate }, set: { player.setRate($0) })) {
            ForEach([Float(0.75), 1, 1.25, 1.5, 2], id: \.self) {
              Text(String(format: "%gx", $0)).tag($0)
            }
          }
        }
      }
      if player.loading { ProgressView() }
      if !player.error.isEmpty { Failure(message: player.error) }
    }.onChange(of: date) { _, _ in player.stop() }.onDisappear { player.stop() }
      .task {
        while !Task.isCancelled {
          player.updateClock()
          do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
        }
      }
      .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification))
    { note in
      if (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
        == AVAudioSession.InterruptionType.began.rawValue
      {
        player.pause()
      }
    }
  }
  private func clock(_ seconds: Double) -> String {
    String(format: "%d:%02d", Int(seconds) / 60, Int(seconds) % 60)
  }
}
