import SwiftUI
import UniformTypeIdentifiers

struct ResearchContext: ViewModifier {
  let context: String
  @EnvironmentObject private var session: AppSession
  func body(content: Content) -> some View {
    content.toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button {
          if session.authenticated { session.research.present(context: context) } else { session.showingLogin = true }
        } label: {
          Image(systemName: "bubble.left.and.text.bubble.right").foregroundStyle(Theme.teal).frame(width: 32, height: 32)
        }.accessibilityLabel("Research")
      }
    }
  }
}

struct ResearchSheet: View {
  let context: String
  var initialRunID = ""
  @EnvironmentObject private var activity: ResearchActivityStore
  @State private var path: [String] = []
  @State private var navigated = false
  var body: some View {
    VStack(spacing: 0) {
      ResearchCompletionBanner(activity: activity)
      NavigationStack(path: $path) {
        ResearchView(context: context)
          .navigationDestination(for: String.self) { ResearchRunView(runId: $0) }
          .task { if !navigated { navigated = true; if !initialRunID.isEmpty { path = [initialRunID] } } }
      }
    }.background(Theme.background).tint(Theme.teal).preferredColorScheme(.dark)

  }
}

private struct ResearchSheetControls: ViewModifier {
  @EnvironmentObject private var activity: ResearchActivityStore
  func body(content: Content) -> some View {
    content.toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button("Close") { activity.presentation = nil }
      }
    }
  }
}

struct ResearchComposer: View {
  var context = ""
  var parentRunId = ""
  var conversationId = ""
  let onCreated: (ResearchRun) -> Void
  @EnvironmentObject private var session: AppSession
  @State private var query = ""
  @State private var submitting = false
  @State private var error = ""
  @State private var pendingQuery = ""
  @State private var idempotencyKey = ""
  @FocusState private var focused: Bool
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(alignment: .bottom, spacing: 12) {
        TextField(
          parentRunId.isEmpty && conversationId.isEmpty ? "Ask Zopedia" : "Message Zopedia", text: $query, axis: .vertical
        )
        .lineLimit(2...6).focused($focused).accessibilityIdentifier("research.query")
        Button(action: submit) {
          if submitting {
            ProgressView()
          } else {
            Image(systemName: "arrow.up").fontWeight(.semibold).frame(width: 24, height: 24)
          }
        }.buttonStyle(.borderedProminent).disabled(
          submitting || query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || query.count > 4000
        )
        .accessibilityLabel("Start research").accessibilityIdentifier("research.submit")
      }.padding(14).background(Theme.panel, in: RoundedRectangle(cornerRadius: 14)).overlay(
        RoundedRectangle(cornerRadius: 14).stroke(Theme.border))
      if !error.isEmpty { Failure(message: error) }
    }
  }
  private func submit() {
    guard !submitting else { return }
    let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return }
    let request = text
    if request != pendingQuery {
      pendingQuery = request
      idempotencyKey = "ios:\(UUID().uuidString)"
    }
    submitting = true
    error = ""
    focused = false
    Task {
      do {
        var body: [String: JSONValue] = ["query": .string(request), "parent_run_id": .string(parentRunId),
          "conversation_id": .string(conversationId), "timezone": .string(TimeZone.current.identifier)]
        if !context.isEmpty {
          body["visible_artifacts"] = .array([.object(["kind": .string("workspace"), "summary": .string(context)])])
        }
        let run: ResearchRun = try await session.api.post(
          "/api/research-runs", body: body, key: idempotencyKey)
        query = ""
        pendingQuery = ""
        idempotencyKey = ""
        session.research.watch(run)
        onCreated(run)
      } catch { self.error = error.localizedDescription }
      submitting = false
    }
  }
}

struct ResearchView: View {
  var context = ""
  @EnvironmentObject private var session: AppSession
  @StateObject private var threads = Resource<[ResearchConversationSummary]>()
  @State private var tab = "threads"
  @State private var selectedRun: ResearchRun?
  @State private var selectedConversation: ResearchConversationSummary?
  @State private var threadSearch = ""
  var body: some View {
    VStack(spacing: 0) {
      Picker("Research view", selection: $tab) {
        Text("Conversations").tag("threads")
        Text("Knowledge").tag("knowledge")
        Text("Sources").tag("sources")
      }.pickerStyle(.segmented).padding(.horizontal, 20).padding(.bottom, 12)
      if tab != "threads" {
        KnowledgeView(sourcesOnly: tab == "sources").id(tab)
      } else {
        ScrollView {
          LazyVStack(alignment: .leading, spacing: 20) {
            ResearchComposer(context: context) { selectedRun = $0 }
            ResourceStatus(resource: threads) { Task { await load() } }
            TextField("Search conversations", text: $threadSearch).textFieldStyle(.roundedBorder)
            ForEach(
              (threads.value ?? []).filter {
                threadSearch.isEmpty || $0.title.localizedCaseInsensitiveContains(threadSearch)
              }
            ) { run in
              Button {
                selectedConversation = run
              } label: {
                VStack(alignment: .leading, spacing: 8) {
                  Text(run.title).font(.subheadline.weight(.medium)).foregroundStyle(Theme.text)
                    .lineLimit(3)
                  HStack {
                    Text(run.status.replacingOccurrences(of: "_", with: " ")).foregroundStyle(
                      ["completed", "failed", "cancelled"].contains(run.status) ? .secondary : Theme.teal)
                    Spacer()
                    if let date = Format.date(run.updatedAt) {
                      Text(date, style: .relative).foregroundStyle(.secondary)
                    }
                  }.font(.caption)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8)
                  .contentShape(Rectangle())
              }.buttonStyle(.plain)
              Divider()
            }
          }.padding(20)
        }.refreshable { await load() }
      }
    }
    .navigationTitle("Zopedia").navigationBarTitleDisplayMode(.inline).workbench().modifier(ResearchSheetControls()).task { await load() }
    .navigationDestination(item: $selectedRun) { run in ResearchRunView(runId: run.runId) }
    .navigationDestination(item: $selectedConversation) { item in ResearchRunView(conversationId: item.conversationId) }
  }
  private func load() async {
    await threads.load { try await session.api.get("/api/research-conversations", query: ["limit": "60"]) }
  }
}

struct ResearchRunView: View {
  var runId = ""
  var conversationId = ""
  @EnvironmentObject private var session: AppSession
  @EnvironmentObject private var activity: ResearchActivityStore
  @State private var resolvedId = ""
  @State private var error = ""
  @State private var cancelling = false
  @State private var revision = 0
  private var conversation: ResearchConversation? { activity.conversations[resolvedId] }
  private var activeRun: ResearchRun? {
    conversation?.turns.flatMap(\.attempts).map { activity.runs[$0.runId] ?? $0 }.first { !$0.isTerminal }
  }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 28) {
        if let conversation {
          ForEach(conversation.turns) { turn in
            VStack(alignment: .leading, spacing: 16) {
              Text(turn.query).font(.subheadline.weight(.medium)).textSelection(.enabled)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                .background(Theme.panel, in: RoundedRectangle(cornerRadius: 14))
              if let attempt = turn.attempts.last {
                let run = activity.runs[attempt.runId] ?? attempt
                ResearchActivityView(events: activity.journals[run.runId] ?? [], active: !run.isTerminal,
                  status: run.status, checkedAt: activity.checkedAt[run.runId])
                if let message = activity.errors[run.runId] { Text(message).font(.caption).foregroundStyle(Theme.coral) }
                if let result = run.result, !result.answerMarkdown.isEmpty {
                  MarkdownView(text: result.answerMarkdown)
                  ForEach(result.visualizations ?? [], id: \.researchChartID) { artifact in
                    ResearchChart(artifact: artifact)
                  }
                  if !result.citations.isEmpty {
                    DisclosureGroup("Sources · \(result.citations.count)") {
                      ForEach(result.citations) { source in
                        if let url = URL(string: source.url), ["http", "https"].contains(url.scheme ?? "") {
                          Link(source.label.isEmpty ? url.host ?? "Source" : source.label, destination: url).font(.caption)
                        }
                      }
                    }.font(.caption)
                  }
                } else if run.isTerminal {
                  Text(run.status == "cancelled" ? "Research cancelled." : "This reply did not complete. You can continue below.")
                    .font(.subheadline).foregroundStyle(.secondary)
                }
                if !run.isTerminal {
                  Button("Stop") { Task { await cancel(run) } }.font(.caption)
                    .disabled(cancelling || run.status == "cancel_requested")
                }
              }
            }
          }
        } else if error.isEmpty { ProgressView().frame(maxWidth: .infinity) }
        if !error.isEmpty { Failure(message: error, retry: { revision += 1 }) }
      }.padding(20)
    }.safeAreaInset(edge: .bottom) {
      if !resolvedId.isEmpty {
        ResearchComposer(conversationId: resolvedId) { _ in revision += 1 }
          .disabled(activeRun != nil).padding(.horizontal, 16).padding(.vertical, 10).background(Theme.background)
      }
    }.navigationTitle("Conversation").navigationBarTitleDisplayMode(.inline).workbench().modifier(ResearchSheetControls())
      .task(id: "\(runId)|\(conversationId)|\(revision)") { await load() }
      .refreshable { await load() }
  }
  private func load() async {
    do {
      resolvedId = try await activity.loadConversation(id: conversationId.isEmpty ? resolvedId : conversationId, runID: runId)
      error = ""
    } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
  }
  private func cancel(_ run: ResearchRun) async {
    cancelling = true
    do {
      let next: ResearchRun = try await session.api.post("/api/research-runs/\(run.runId)/cancel")
      activity.watch(next)
    } catch { self.error = error.localizedDescription }
    cancelling = false
  }
}

struct ResearchActivityView: View {
  @State private var expanded = false
  let events: [ResearchEvent]
  let active: Bool
  var status = "running"
  var checkedAt: Date? = nil
  private var updates: [ResearchEvent] {
    events.filter { $0.payload.publicUpdate != nil }
  }
  private var sources: [ResearchRun.Result.Citation] {
    var seen = Set<String>()
    return events.flatMap { $0.payload.sourceLinks ?? [] }.filter { seen.insert($0.url).inserted }
  }
  private var activity: String {
    events.reversed().compactMap { $0.payload.activityLabel }.first ?? "Researching"
  }
  var body: some View {
    if active {
      VStack(alignment: .leading, spacing: 10) {
        HStack {
          ProgressView().controlSize(.small)
          Text(updates.isEmpty ? (status == "queued" ? "Queued" : activity) : activity).font(.subheadline.weight(.medium))
          Spacer()
          if let checkedAt { Text(checkedAt, format: .dateTime.hour().minute().second()).font(.caption2).monospacedDigit().foregroundStyle(.secondary) }
        }
        if let update = updates.last?.payload.publicUpdate {
          if !update.summary.isEmpty { Text(update.summary).font(.subheadline) }
          if !update.finding.isEmpty { Text(update.finding).font(.subheadline) }
          if !update.nextStep.isEmpty { Text(update.nextStep).font(.caption).foregroundStyle(.secondary) }
        }
        if !sources.isEmpty {
          Text("\(sources.count) \(sources.count == 1 ? "source" : "sources") · \(updates.count) updates")
            .font(.caption2).foregroundStyle(.secondary)
        }
      }.padding(14).background(Theme.panel, in: RoundedRectangle(cornerRadius: 12))
    }
    if !updates.isEmpty {
      DisclosureGroup("Research activity", isExpanded: $expanded) {
        VStack(alignment: .leading, spacing: 14) {
          ForEach(updates) { event in
            if let update = event.payload.publicUpdate {
              VStack(alignment: .leading, spacing: 6) {
                if !update.summary.isEmpty { Text(update.summary).foregroundStyle(.secondary) }
                if !update.finding.isEmpty { Text(update.finding) }
              }.font(.caption).textSelection(.enabled)
            }
          }
          if active, let next = updates.last?.payload.publicUpdate?.nextStep, !next.isEmpty {
            Text(next).font(.caption).foregroundStyle(.secondary)
          }
          ForEach(sources) { source in
            if let url = URL(string: source.url), ["http", "https"].contains(url.scheme ?? "") {
              Link(source.label.isEmpty ? url.host ?? "Source" : source.label, destination: url).font(.caption)
            }
          }
        }.padding(.top, 12)
      }.font(.caption).tint(Theme.teal)
    }
  }
}

struct KnowledgeView: View {
  var sourcesOnly = false
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<KnowledgeWorkspace>()
  @State private var search = ""
  @State private var folder = ""
  @State private var breadcrumbs: [(String, String)] = []
  @State private var offset = 0
  @State private var importing = false
  @State private var uploading = false
  @State private var uploadError = ""
  @State private var uploadNotice = ""
  private var key: String { "\(folder)|\(offset)|\(search)" }
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 16) {
        HStack {
          TextField("Search knowledge", text: $search).textFieldStyle(.roundedBorder).submitLabel(
            .search
          ).onSubmit {
            folder = ""
            offset = 0
            breadcrumbs = []
            Task { await load() }
          }
          Button {
            importing = true
          } label: {
            Image(systemName: "paperclip")
          }.disabled(uploading).accessibilityLabel("Upload source")
        }
        if uploading { ProgressView("Uploading") }
        if !uploadError.isEmpty { Failure(message: uploadError) }
        if !uploadNotice.isEmpty { Text(uploadNotice).font(.caption).foregroundStyle(Theme.teal) }
        if !folder.isEmpty {
          Button {
            let previous = breadcrumbs.popLast() ?? ("", "Knowledge")
            folder = previous.0
            offset = 0
          } label: {
            Label("Back", systemImage: "chevron.left")
          }
        }
        ResourceStatus(resource: resource) { Task { await load() } }
        if let workspace = resource.value {
          if !workspace.folderName.isEmpty { Text(workspace.folderName).font(.headline) }
          ForEach(
            sourcesOnly || !search.isEmpty
              ? [] : workspace.folders.filter { !$0.path.hasPrefix("wiki/sources") }
          ) { child in
            Button {
              breadcrumbs.append((folder, workspace.folderName))
              folder = child.path
              offset = 0
            } label: {
              HStack {
                Label(child.name, systemImage: "folder")
                Spacer()
                Image(systemName: "chevron.right").font(.caption)
              }.padding(.vertical, 8)
            }.buttonStyle(.plain)
          }
          ForEach(pages(workspace)) { page in
            NavigationLink {
              KnowledgeReader(pageId: page.pageId)
            } label: {
              VStack(alignment: .leading, spacing: 6) {
                Text(page.title).font(.subheadline.weight(.medium)).foregroundStyle(Theme.text)
                if !page.summary.isEmpty {
                  Text(page.summary).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
              }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8)
            }.buttonStyle(.plain)
            Divider()
          }
          if sourcesOnly || !search.isEmpty {
            ForEach(Array(workspace.documents.enumerated()), id: \.offset) { _, document in
              Panel(document.text("title")) {
                if !document.text("excerpt").isEmpty {
                  MarkdownView(text: document.text("excerpt"))
                }
                Text("\(document.text("source_kind")) · \(document.text("provider"))").font(
                  .caption2
                ).foregroundStyle(.secondary)
                Text(
                  document.text("published_at").isEmpty
                    ? document.text("as_of") : document.text("published_at")
                ).font(.caption2).foregroundStyle(.secondary)
                if let url = URL(string: document.text("url")),
                  ["http", "https"].contains(url.scheme ?? "")
                {
                  Link("Read source", destination: url)
                }
              }
            }
          }
          if session.context?.isAdmin == true, sourcesOnly {
            if !workspace.maintenanceReports.isEmpty {
              Panel("Maintenance") {
                WorkbookTable(
                  rows: workspace.maintenanceReports,
                  columns: [
                    .init(key: "status", title: "Status"),
                    .init(key: "page_count", title: "Pages", style: .number),
                    .init(key: "edge_count", title: "Links", style: .number),
                    .init(key: "issue_count", title: "Issues", style: .number),
                    .init(key: "created_at_utc", title: "Time"),
                  ])
              }
            }
            if !workspace.proposals.isEmpty {
              Panel("Open proposals") {
                ForEach(Array(workspace.proposals.enumerated()), id: \.offset) { _, proposal in
                  DisclosureGroup(proposal.text("title")) { RecordDetails(record: proposal) }
                }
              }
            }
          }
          HStack {
            if offset > 0 { Button("Previous") { offset = max(0, offset - 60) } }
            Spacer()
            if workspace.hasMore { Button("Next") { offset += 60 } }
          }
        }
      }.padding(20)
    }.workbench()
      .task(id: "\(folder)|\(offset)") { await load() }
      .refreshable { await load() }
      .fileImporter(
        isPresented: $importing,
        allowedContentTypes: [
          .plainText, .commaSeparatedText, .json, .pdf,
          UTType(filenameExtension: "md") ?? .plainText,
        ]
      ) { result in
        if case let .success(url) = result {
          uploading = true
          uploadError = ""
          uploadNotice = ""
          Task {
            do {
              let _: JSONValue = try await session.api.upload(url)
              uploadNotice = "Source stored"
              await load()
            } catch { uploadError = error.localizedDescription }
            uploading = false
          }
        } else if case let .failure(error) = result {
          uploadError = error.localizedDescription
        }
      }
  }
  private func pages(_ workspace: KnowledgeWorkspace) -> [KnowledgePage] {
    let candidates = sourcesOnly ? workspace.sourcePages : workspace.pages
    var seen = Set<String>()
    return candidates.filter { seen.insert($0.pageId).inserted }
  }
  private func load() async {
    await resource.load(key: key, clearBeforeLoad: true, clearOnFailure: true) {
      try await session.api.get(
        "/api/zopedia/workspace",
        query: ["q": search, "folder": folder, "offset": String(offset), "limit": "60"])
    }
  }
}

struct KnowledgeReader: View {
  let pageId: String
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<KnowledgePage>()
  @State private var selectedPage: String?
  private func linkIndex(_ page: KnowledgePage) -> [String: String] {
    var index: [String: String] = [:]
    for linked in [page] + (page.links ?? []) {
      for key in [linked.pageId, linked.title, linked.slug ?? ""] where !key.isEmpty {
        index[key.lowercased()] = linked.pageId
      }
    }
    return index
  }
  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 20) {
        ResourceStatus(resource: resource) { Task { await load() } }
        if let page = resource.value {
          Text(page.title).font(.title2.weight(.semibold))
          if let version = page.version {
            Text("v\(version) · \(page.updatedAt ?? "")").font(.caption2).foregroundStyle(
              .secondary)
          }
          MarkdownView(
            text: page.bodyMarkdown ?? page.summary, internalLinks: linkIndex(page),
            onPage: { selectedPage = $0 })
          if let links = page.links, !links.isEmpty {
            Panel("Connected pages") {
              ForEach(Array(links.enumerated()), id: \.offset) { _, link in
                Button {
                  selectedPage = link.pageId
                } label: {
                  VStack(alignment: .leading, spacing: 4) {
                    Text(link.direction == "backlink" ? "Linked from" : "Links to").font(.caption2)
                      .foregroundStyle(.secondary)
                    Text(link.title).font(.subheadline)
                  }.padding(.vertical, 5)
                }
              }
            }
          }
          ForEach(page.sourceUrls ?? [], id: \.self) { raw in
            if let url = URL(string: raw), ["https", "http"].contains(url.scheme ?? "") {
              Link(url.host ?? raw, destination: url).font(.caption)
            }
          }
        }
      }.padding(20)
    }.navigationTitle("Knowledge").navigationBarTitleDisplayMode(.inline).workbench()
      .researchContext("Zopedia page; page_id=\(pageId)")
      .task { await load() }
      .navigationDestination(
        isPresented: Binding(get: { selectedPage != nil }, set: { if !$0 { selectedPage = nil } })
      ) {
        if let selectedPage { KnowledgeReader(pageId: selectedPage) }
      }
  }
  private func load() async {
    await resource.load(key: pageId) {
      try await session.api.get("/api/zopedia/pages", query: ["page_id": pageId])
    }
  }
}
