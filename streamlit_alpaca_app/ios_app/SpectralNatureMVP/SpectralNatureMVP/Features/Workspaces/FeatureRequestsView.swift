import SwiftUI

struct FeatureRequestSummary: Decodable, Identifiable {
  let id: String
  let title: String
  let ownerName: String
  let updatedAt: String
  let messageCount: Int
}

struct FeatureRequestList: Decodable {
  let canReviewAll: Bool
  let requests: [FeatureRequestSummary]
}

struct FeatureRequestThread: Decodable, Identifiable {
  struct Message: Decodable, Identifiable {
    let id: String
    let authorId: String
    let authorName: String
    let authorRole: String
    let body: String
    let createdAt: String
  }
  let id: String
  let title: String
  let ownerName: String
  let appVersion: String
  let createdAt: String
  let messages: [Message]
}

struct FeatureRequestsView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<FeatureRequestList>()
  @State private var scope = "mine"
  @State private var showingNew = false
  @State private var draftBody = ""
  @State private var draftID = UUID().uuidString

  var body: some View {
    List {
      if resource.value?.canReviewAll == true {
        Picker("Requests", selection: $scope) {
          Text("Mine").tag("mine")
          Text("All requests").tag("all")
        }.pickerStyle(.segmented)
      }
      ResourceStatus(resource: resource) { Task { await load() } }
      if let page = resource.value {
        if page.requests.isEmpty {
          Text("No requests yet").foregroundStyle(.secondary)
        }
        ForEach(page.requests) { request in
          NavigationLink {
            FeatureRequestThreadView(requestID: request.id)
          } label: {
            VStack(alignment: .leading, spacing: 7) {
              Text(request.title).font(.headline)
              HStack {
                if scope == "all" { Text(request.ownerName) }
                Text("\(request.messageCount) \(request.messageCount == 1 ? "message" : "messages")")
                Spacer()
                if let date = Format.date(request.updatedAt) { Text(date, style: .date) }
              }.font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical, 5)
          }
        }
      }
    }.scrollContentBackground(.hidden).workbench()
      .navigationTitle("Feature requests").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .topBarTrailing) {
          Button { showingNew = true } label: { Image(systemName: "plus") }
            .accessibilityLabel("New feature request")
        }
      }
      .task(id: scope) { await load() }
      .refreshable { await load() }
      .sheet(isPresented: $showingNew) {
        NavigationStack {
          NewFeatureRequestView(bodyText: $draftBody, requestID: draftID) {
            draftBody = ""
            draftID = UUID().uuidString
            showingNew = false
            Task { await load() }
          }
        }.preferredColorScheme(.dark).tint(Theme.teal)
      }
  }
  private func load() async {
    let selected = scope
    await resource.load(key: selected, clearOnFailure: true) {
      try await session.api.get("/api/feature-requests", query: ["scope": selected])
    }
  }
}

private struct NewFeatureRequestView: View {
  @EnvironmentObject private var session: AppSession
  @Environment(\.dismiss) private var dismiss
  @Binding var bodyText: String
  let requestID: String
  let onCreated: () -> Void
  @State private var sending = false
  @State private var error = ""

  var body: some View {
    Form {
      Section { TextEditor(text: $bodyText).frame(minHeight: 240).accessibilityLabel("Feedback").accessibilityIdentifier("request.message") }
      if bodyText.unicodeScalars.count > 8000 { Text("Messages have an 8,000 character limit.").foregroundStyle(Theme.coral) }
      if !error.isEmpty { Text(error).foregroundStyle(Theme.coral).font(.callout) }
    }.disabled(sending).scrollContentBackground(.hidden).workbench()
      .navigationTitle("Feedback").navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(sending) }
        ToolbarItem(placement: .confirmationAction) {
          Button { Task { await submit() } } label: {
            if sending { ProgressView() } else { Text("Send") }
          }.disabled(sending || bodyText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || bodyText.unicodeScalars.count > 8000)
        }
      }.interactiveDismissDisabled(sending)
  }
  private func submit() async {
    sending = true
    error = ""
    defer { sending = false }
    let info = Bundle.main.infoDictionary ?? [:]
    let version = "\(info["CFBundleShortVersionString"] as? String ?? "") (\(info["CFBundleVersion"] as? String ?? ""))"
    let message = bodyText.trimmingCharacters(in: .whitespacesAndNewlines)
    do {
      let _: FeatureRequestThread = try await session.api.post("/api/feature-requests", body: [
        "id": .string(requestID), "title": .string(String(String.UnicodeScalarView(message.unicodeScalars.prefix(200)))), "body": .string(message),
        "client": .string("ios"), "app_version": .string(version),
      ])
      onCreated()
    } catch { self.error = error.localizedDescription }
  }
}

struct FeatureRequestThreadView: View {
  let requestID: String
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<FeatureRequestThread>()
  @State private var message = ""
  @State private var messageID = UUID().uuidString
  @State private var sending = false
  @State private var sendError = ""

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView {
        LazyVStack(alignment: .leading, spacing: 18) {
          ResourceStatus(resource: resource) { Task { await load() } }
          if let thread = resource.value {
            if !(thread.messages.first?.body.hasPrefix(thread.title) ?? false) { Text(thread.title).font(.title2.weight(.semibold)) }
            ForEach(thread.messages) { item in
              VStack(alignment: .leading, spacing: 9) {
                HStack {
                  Text(item.authorName).font(.caption.weight(.semibold))
                  if item.authorRole == "admin" { Text("Admin").font(.caption2).foregroundStyle(Theme.violet) }
                  Spacer()
                  if let date = Format.date(item.createdAt) {
                    Text(date.formatted(date: .abbreviated, time: .shortened))
                      .font(.caption2).foregroundStyle(.secondary)
                  }
                }
                Text(item.body).font(.body).textSelection(.enabled)
              }.frame(maxWidth: .infinity, alignment: .leading).padding(14)
                .background(item.authorId == session.context?.userId ? Theme.teal.opacity(0.08) : Theme.panel,
                  in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.border))
                .id(item.id)
            }
          }
          Color.clear.frame(height: 1).id("latest-message")
        }.padding(20)
      }.onChange(of: resource.value?.messages.last?.id) { _, _ in
        withAnimation { proxy.scrollTo("latest-message", anchor: .bottom) }
      }.refreshable { await load() }
    }
    .safeAreaInset(edge: .bottom) {
      VStack(alignment: .leading, spacing: 8) {
        if !sendError.isEmpty { Text(sendError).font(.caption).foregroundStyle(Theme.coral) }
        if message.unicodeScalars.count > 8000 { Text("Messages have an 8,000 character limit.").font(.caption).foregroundStyle(Theme.coral) }
        HStack(alignment: .bottom, spacing: 12) {
          TextField("Add a message", text: $message, axis: .vertical).lineLimit(1...5)
            .padding(10).background(Theme.background, in: RoundedRectangle(cornerRadius: 9))
            .disabled(sending)
          Button { Task { await send() } } label: {
            if sending { ProgressView() }
            else { Image(systemName: "arrow.up.circle.fill").font(.title) }
          }.accessibilityLabel("Send message")
            .disabled(sending || resource.value == nil || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || message.unicodeScalars.count > 8000)
        }
      }.padding(12).background(Theme.panel)
    }
    .navigationTitle("Feature request").navigationBarTitleDisplayMode(.inline).workbench()
    .task(id: requestID) { await load() }
  }
  private func load() async {
    await resource.load(key: requestID) {
      try await session.api.get("/api/feature-requests/\(requestID)")
    }
  }
  private func send() async {
    sending = true
    sendError = ""
    defer { sending = false }
    do {
      let saved: FeatureRequestThread = try await session.api.post(
        "/api/feature-requests/\(requestID)/messages",
        body: ["id": .string(messageID), "body": .string(message)])
      await resource.load(key: requestID) { saved }
      message = ""
      messageID = UUID().uuidString
    } catch { sendError = error.localizedDescription }
  }
}
