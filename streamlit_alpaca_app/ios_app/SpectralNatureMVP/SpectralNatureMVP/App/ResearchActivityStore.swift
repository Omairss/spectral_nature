import Combine
import Foundation
import SwiftUI

@MainActor
final class ResearchActivityStore: ObservableObject {
  struct Presentation: Identifiable {
    let id = UUID()
    var context = ""
    var runID = ""
  }
  struct Completion: Identifiable {
    let id: String
    let status: String
    var title: String { status == "completed" ? "Research complete" : status == "cancelled" ? "Research cancelled" : "Research stopped" }
  }
  @Published var presentation: Presentation?
  @Published var completion: Completion?
  @Published private(set) var runs: [String: ResearchRun] = [:]
  @Published private(set) var conversations: [String: ResearchConversation] = [:]
  @Published private(set) var journals: [String: [ResearchEvent]] = [:]
  @Published private(set) var checkedAt: [String: Date] = [:]
  @Published private(set) var errors: [String: String] = [:]
  private let api: APIClient
  private var owner = ""
  private var tracked = Set<String>()
  private var delivered = Set<String>()
  private var persistenceKey: String { "research.activity.\(AppEnvironment.serverURL.absoluteString).\(owner)" }

  init(api: APIClient) { self.api = api }

  func setOwner(_ userID: String) {
    guard owner != userID else { return }
    owner = userID
    runs = [:]; conversations = [:]; journals = [:]; checkedAt = [:]; errors = [:]
    completion = nil; presentation = nil
    tracked = userID.isEmpty ? [] : Set(UserDefaults.standard.stringArray(forKey: persistenceKey + ".active") ?? [])
    delivered = userID.isEmpty ? [] : Set(UserDefaults.standard.stringArray(forKey: persistenceKey + ".delivered") ?? [])
  }
  private func persist() {
    guard !owner.isEmpty else { return }
    UserDefaults.standard.set(Array(tracked).sorted(), forKey: persistenceKey + ".active")
    UserDefaults.standard.set(Array(delivered).sorted().suffix(200).map { $0 }, forKey: persistenceKey + ".delivered")
  }
  func present(context: String = "", runID: String = "") {
    presentation = Presentation(context: context, runID: runID)
    if completion?.id == runID { completion = nil }
  }
  func watch(_ run: ResearchRun) {
    guard !owner.isEmpty else { return }
    runs[run.runId] = run
    if !run.isTerminal { tracked.insert(run.runId); persist() }
  }
  func receiveCompletion(runID: String, status: String) {
    guard !owner.isEmpty, delivered.insert(runID).inserted else { return }
    completion = Completion(id: runID, status: status)
    persist()
  }
  func loadConversation(id: String = "", runID: String = "") async throws -> String {
    let account = owner
    guard !account.isEmpty else { throw APIError.server(401, "Sign in to view research.") }
    var resolved = id
    if resolved.isEmpty {
      let run: ResearchRun = try await api.get("/api/research-runs/\(runID)")
      guard account == owner else { throw CancellationError() }
      resolved = run.conversationId
      watch(run)
    }
    let conversation: ResearchConversation = try await api.get("/api/research-conversations/\(resolved)")
    guard account == owner else { throw CancellationError() }
    conversations[resolved] = conversation
    for run in conversation.turns.flatMap(\.attempts) {
      watch(run)
      try await loadEvents(run, owner: account)
    }
    return resolved
  }
  private func loadEvents(_ run: ResearchRun, owner account: String) async throws {
    var entries = journals[run.runId] ?? []
    while (entries.last?.sequence ?? 0) < run.lastEventSequence {
      let after = entries.last?.sequence ?? 0
      let page: ResearchEvents = try await api.get("/api/research-runs/\(run.runId)/events",
        query: ["after_sequence": String(after)])
      guard account == owner else { throw CancellationError() }
      let new = page.events.filter { $0.sequence > after }
      guard !new.isEmpty else { break }
      let known = Set(entries.map(\.eventId))
      entries.append(contentsOf: new.filter { !known.contains($0.eventId) })
      entries.sort { $0.sequence < $1.sequence }
    }
    guard account == owner else { throw CancellationError() }
    journals[run.runId] = entries
  }
  func monitor() async {
    let account = owner
    guard !account.isEmpty else { return }
    do {
      let recent: [ResearchConversationSummary] = try await api.get("/api/research-conversations", query: ["limit": "60"])
      guard account == owner else { return }
      for thread in recent where !["completed", "failed", "cancelled"].contains(thread.status) {
        tracked.insert(thread.latestRunId)
      }
      persist()
    } catch { /* Retained active IDs still allow recovery when the list is unavailable. */ }
    let clock = ContinuousClock()
    while !Task.isCancelled, account == owner {
      let deadline = clock.now.advanced(by: .seconds(5))
      for id in Array(tracked).sorted() {
        guard !Task.isCancelled, account == owner else { return }
        do {
          let run: ResearchRun = try await api.get("/api/research-runs/\(id)")
          guard account == owner, !Task.isCancelled else { return }
          try await loadEvents(run, owner: account)
          runs[id] = run
          checkedAt[id] = Date()
          errors[id] = nil
          if run.isTerminal {
            tracked.remove(id)
            receiveCompletion(runID: id, status: run.status)
            persist()
          }
        } catch {
          if !Task.isCancelled, account == owner { errors[id] = error.localizedDescription }
        }
      }
      do { try await clock.sleep(until: deadline) } catch { return }
    }
  }
}

struct ResearchPresentationHost: ViewModifier {
  @ObservedObject var activity: ResearchActivityStore
  func body(content: Content) -> some View {
    VStack(spacing: 0) {
      ResearchCompletionBanner(activity: activity)
      content
    }.sheet(item: $activity.presentation) { item in
      ResearchSheet(context: item.context, initialRunID: item.runID).environmentObject(activity)
    }

  }
}


struct ResearchCompletionBanner: View {
  @ObservedObject var activity: ResearchActivityStore
  var body: some View {
    if let notice = activity.completion {
      HStack(spacing: 12) {
        Button { activity.present(runID: notice.id) } label: {
          Label(notice.title, systemImage: notice.status == "completed" ? "checkmark.circle" : "exclamationmark.circle")
            .frame(maxWidth: .infinity, alignment: .leading)
        }.buttonStyle(.plain)
        Button { activity.completion = nil } label: { Image(systemName: "xmark") }
          .accessibilityLabel("Dismiss research notification")
      }.font(.subheadline).padding(16).background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border)).padding(.horizontal, 16)
        .padding(.vertical, 8).foregroundStyle(Theme.text).background(Theme.background)
        .accessibilityElement(children: .contain)
    }
  }
}
