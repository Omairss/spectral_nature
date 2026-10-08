import SwiftUI
import UniformTypeIdentifiers

struct ResearchContext: ViewModifier {
  let context: String
  @EnvironmentObject private var session: AppSession
  @State private var showingResearch = false
  func body(content: Content) -> some View {
    content.toolbar {
      ToolbarItem(placement: .topBarTrailing) {
        Button {
          if session.authenticated { showingResearch = true } else { session.showingLogin = true }
        } label: {
          Image(systemName: "bubble.left.and.text.bubble.right")
        }.accessibilityLabel("Research this view")
      }
    }
    .sheet(isPresented: $showingResearch) { ResearchSheet(context: context) }
  }
}

struct ResearchSheet: View {
  let context: String
  var parentRunId: String = ""
  @Environment(\.dismiss) private var dismiss
  @State private var path: [String] = []
  var body: some View {
    NavigationStack(path: $path) {
      VStack(alignment: .leading, spacing: 20) {
        Text(context).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
        ResearchComposer(context: context, parentRunId: parentRunId) { path.append($0.runId) }
        Spacer()
      }.padding(20).navigationTitle("Research").navigationBarTitleDisplayMode(.inline)
        .workbench()
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        .navigationDestination(for: String.self) { ResearchRunView(runId: $0) }
    }.tint(Theme.teal).preferredColorScheme(.dark)
  }
}

struct ResearchComposer: View {
  var context = ""
  var parentRunId = ""
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
          parentRunId.isEmpty ? "Ask Zopedia" : "Ask a follow-up", text: $query, axis: .vertical
        )
        .lineLimit(2...6).focused($focused).accessibilityIdentifier("research.query")
        Button(action: submit) {
          if submitting {
            ProgressView()
          } else {
            Image(systemName: "arrow.up").fontWeight(.semibold).frame(width: 24, height: 24)
          }
        }.buttonStyle(.borderedProminent).disabled(
          submitting || query.trimmingCharacters(in: .whitespacesAndNewlines).count < 3
            || query.count + context.count > 3900
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
    guard text.count >= 3 else { return }
    let request = context.isEmpty ? text : "\(text)\n\nCurrent workspace: \(context)"
    if request != pendingQuery {
      pendingQuery = request
      idempotencyKey = "ios:\(UUID().uuidString)"
    }
    submitting = true
    error = ""
    focused = false
    Task {
      do {
        let run: ResearchRun = try await session.api.post(
          "/api/research-runs",
          body: ["query": .string(request), "parent_run_id": .string(parentRunId)],
          key: idempotencyKey)
        query = ""
        pendingQuery = ""
        idempotencyKey = ""
        onCreated(run)
      } catch { self.error = error.localizedDescription }
      submitting = false
    }
  }
}

struct ResearchView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var threads = Resource<[ResearchRun]>()
  @State private var tab = "threads"
  @State private var selectedRun: ResearchRun?
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
            ResearchComposer { selectedRun = $0 }
            ResourceStatus(resource: threads) { Task { await load() } }
            TextField("Search conversations", text: $threadSearch).textFieldStyle(.roundedBorder)
            ForEach(
              (threads.value ?? []).filter {
                threadSearch.isEmpty || $0.query.localizedCaseInsensitiveContains(threadSearch)
              }
            ) { run in
              Button {
                selectedRun = run
              } label: {
                VStack(alignment: .leading, spacing: 8) {
                  Text(run.query).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                    .lineLimit(3)
                  HStack {
                    Text(run.status.replacingOccurrences(of: "_", with: " ")).foregroundStyle(
                      run.isTerminal ? .secondary : Theme.teal)
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
    .navigationTitle("Zopedia").workbench().task { await load() }
    .navigationDestination(item: $selectedRun) { run in ResearchRunView(runId: run.runId) }
  }
  private func load() async {
    await threads.load { try await session.api.get("/api/research-runs", query: ["limit": "60"]) }
  }
}

struct ResearchRunView: View {
  let runId: String
  @EnvironmentObject private var session: AppSession
  @Environment(\.scenePhase) private var scenePhase
  @State private var run: ResearchRun?
  @State private var events: [ResearchEvent] = []
  @State private var sequence = 0
  @State private var error = ""
  @State private var cancelling = false
  @State private var followUp = false
  @State private var pollingRevision = 0
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        if let run {
          Text(run.query).font(.title3.weight(.medium)).textSelection(.enabled)
          Text(run.status.replacingOccurrences(of: "_", with: " ")).font(.caption).foregroundStyle(
            Theme.teal)
          if !run.isTerminal {
            Panel {
              ProgressView(value: min(1, max(0, events.last?.progress ?? 0)))
              Text(events.last?.message ?? "Queued").font(.subheadline)
              Button("Cancel research", role: .destructive) { Task { await cancel() } }.disabled(
                cancelling || run.status == "cancel_requested")
            }
          }
          if let result = run.result {
            if !result.answerMarkdown.isEmpty { MarkdownView(text: result.answerMarkdown) }
            if !result.error.isEmpty { Failure(message: result.error) }
            DisclosureGroup("Research details") {
              Metrics(items: [
                ("Provider", result.provider ?? "—"), ("Model", result.model ?? "—"),
                ("Confidence", result.confidence ?? "—"),
              ])
              ForEach(result.limitations ?? [], id: \.self) {
                Text($0).font(.caption).textSelection(.enabled)
              }
            }.font(.subheadline)
            if !result.citations.isEmpty {
              Panel("Sources") {
                ForEach(result.citations) { source in
                  if let url = URL(string: source.url),
                    ["http", "https"].contains(url.scheme ?? "")
                  {
                    Link(destination: url) {
                      Label(
                        source.label.isEmpty ? url.host ?? "Source" : source.label,
                        systemImage: "arrow.up.right"
                      ).font(.subheadline)
                    }
                  }
                }
              }
            }
          }
          if !events.isEmpty {
            DisclosureGroup("Research journal · \(events.count)") {
              VStack(alignment: .leading, spacing: 16) {
                ForEach(events) { event in
                  HStack(alignment: .top, spacing: 12) {
                    Text("\(event.sequence)").font(.caption2.monospacedDigit()).foregroundStyle(
                      .secondary
                    ).frame(width: 24, alignment: .trailing)
                    Text(event.message).font(.caption).textSelection(.enabled)
                  }
                }
              }.padding(.top, 16)
            }.font(.subheadline)
          }
          Button("Follow up") { followUp = true }.buttonStyle(.bordered)
        } else if error.isEmpty {
          ProgressView().frame(maxWidth: .infinity)
        }
        if !error.isEmpty { Failure(message: error, retry: { pollingRevision += 1 }) }
      }.padding(20)
    }
    .navigationTitle("Research").navigationBarTitleDisplayMode(.inline).workbench()
    .task(id: "\(runId)|\(scenePhase == .active)|\(pollingRevision)") {
      guard scenePhase == .active else { return }
      while !Task.isCancelled {
        do {
          try await refresh()
          if run?.isTerminal == true { return }
          try await Task.sleep(for: .milliseconds(1800))
        } catch is CancellationError { return } catch {
          if !Task.isCancelled { self.error = error.localizedDescription }
          return
        }
      }
    }
    .refreshable { do { try await refresh() } catch { self.error = error.localizedDescription } }
    .sheet(isPresented: $followUp) { ResearchSheet(context: "", parentRunId: runId) }
  }
  private func refresh() async throws {
    let next: ResearchRun = try await session.api.get("/api/research-runs/\(runId)")
    try Task.checkCancellation()
    run = next
    let journal: ResearchEvents = try await session.api.get(
      "/api/research-runs/\(runId)/events", query: ["after_sequence": String(sequence)])
    try Task.checkCancellation()
    let known = Set(events.map(\.eventId))
    events.append(contentsOf: journal.events.filter { !known.contains($0.eventId) })
    events.sort { $0.sequence < $1.sequence }
    sequence = max(sequence, journal.lastEventSequence)
    error = ""
  }
  private func cancel() async {
    cancelling = true
    do { run = try await session.api.post("/api/research-runs/\(runId)/cancel") } catch {
      self.error = error.localizedDescription
    }
    cancelling = false
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
                Text(page.title).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
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
    await resource.load(key: key) {
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
