import Charts
import SwiftUI

struct AdminView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var resource = Resource<JSONValue>()
  @State private var section = "Users"
  @State private var days = 7
  @State private var email = ""
  @State private var role = "investor"
  @State private var share = "0"
  @State private var resetEmail = ""
  @State private var working = false
  @State private var actionError = ""
  @State private var notice = ""
  @State private var issuedURL: URL?
  private let sections = ["Users", "Usage & Security", "LLM", "API Keys", "System", "History"]
  var body: some View {
    ScrollView {
      LazyVStack(alignment: .leading, spacing: 20) {
        HStack {
          Picker("Section", selection: $section) { ForEach(sections, id: \.self) { Text($0) } }
          Spacer()
          Picker("History", selection: $days) {
            ForEach([3, 7, 14, 30], id: \.self) { Text("\($0) days").tag($0) }
          }
        }
        ResourceStatus(resource: resource) { Task { await load() } }
        if !actionError.isEmpty { Failure(message: actionError) }
        if !notice.isEmpty { Text(notice).font(.subheadline).foregroundStyle(Theme.teal) }
        if let issuedURL { ShareLink("Share account link", item: issuedURL).privacySensitive() }
        if let value = resource.value {
          switch section {
          case "Users": users(value)
          case "Usage & Security": access(value["access"])
          case "LLM": models(value["llm"])
          case "API Keys":
            table(
              "API keys", value["agent_keys"].records,
              [
                .init(key: "name", title: "Name"), .init(key: "key_prefix", title: "Prefix"),
                .init(key: "scopes", title: "Scopes"), .init(key: "status", title: "Status"),
                .init(key: "created_at", title: "Created"),
                .init(key: "last_used_at", title: "Last used"),
                .init(key: "expires_at", title: "Expires"),
              ])
          case "History":
            table("Job history", value["system"]["job_history"].records, jobColumns)
            table("Dataset history", value["system"]["dataset_history"].records, datasetColumns)
          default: system(value["system"])
          }
        }
      }.padding(20)
    }.navigationTitle("Admin").navigationBarTitleDisplayMode(.inline).workbench()
      .task(id: days) { await load() }.refreshable { await load() }
  }
  private var jobColumns: [WorkbookColumn] {
    [
      .init(key: "job_name", title: "Job"), .init(key: "status", title: "Status"),
      .init(key: "progress_stage", title: "Stage"),
      .init(key: "progress_pct", title: "Progress", style: .number),
      .init(key: "start_time_utc", title: "Started"), .init(key: "end_time_utc", title: "Ended"),
      .init(key: "message", title: "Message"),
    ]
  }
  private var datasetColumns: [WorkbookColumn] {
    [
      .init(key: "dataset_name", title: "Dataset"),
      .init(key: "row_count", title: "Rows", style: .number),
      .init(key: "age_hours", title: "Age, hours", style: .number),
      .init(key: "asof_time_utc", title: "As of"), .init(key: "ingested_at_utc", title: "Ingested"),
    ]
  }
  @ViewBuilder private func users(_ value: JSONValue) -> some View {
    let users = value["users"].records
    Metrics(items: [
      ("Users", String(users.count)),
      (
        "Pending invites",
        String(value["invites"].records.filter { $0.text("status") == "pending" }.count)
      ),
      (
        "Open sessions",
        Format.number(users.reduce(0) { $0 + ($1.number("open_session_count") ?? 0) })
      ),
      (
        "Active sessions",
        Format.number(users.reduce(0) { $0 + ($1.number("active_session_count") ?? 0) })
      ),
    ])
    table(
      "Users", users,
      [
        .init(key: "display_name", title: "Name"), .init(key: "email", title: "Email"),
        .init(key: "role", title: "Role"), .init(key: "membership_role", title: "Portfolio role"),
        .init(key: "share_fraction", title: "Share", style: .fraction),
        .init(key: "status", title: "Status"),
        .init(key: "active_session_count", title: "Active", style: .number),
        .init(key: "last_seen_at", title: "Last seen"),
      ])
    Panel("Invite user") {
      TextField("Email", text: $email).textContentType(.emailAddress).keyboardType(.emailAddress)
        .textInputAutocapitalization(.never).autocorrectionDisabled()
      Picker("Role", selection: $role) {
        Text("Investor").tag("investor")
        Text("Viewer").tag("viewer")
        Text("Admin").tag("admin")
      }
      if role == "investor" {
        HStack {
          Text("Economic share %").font(.subheadline)
          TextField("Share", text: $share).keyboardType(.decimalPad).multilineTextAlignment(
            .trailing)
        }
      }
      Button("Create invite") { Task { await issueInvite() } }.buttonStyle(.borderedProminent)
        .disabled(
          working || !validEmail(email)
            || (role == "investor" && !(Double(share).map { (0...100).contains($0) } ?? false)))
    }
    Panel("Issue password reset") {
      TextField("User email", text: $resetEmail).keyboardType(.emailAddress)
        .textInputAutocapitalization(.never).autocorrectionDisabled()
      Button("Issue reset") { Task { await issueReset() } }.buttonStyle(.bordered).disabled(
        working || !validEmail(resetEmail))
    }
    table(
      "Invitations", value["invites"].records,
      [
        .init(key: "email", title: "Email"), .init(key: "role", title: "Role"),
        .init(key: "proposed_share_fraction", title: "Share", style: .fraction),
        .init(key: "portfolio_slug", title: "Portfolio"), .init(key: "status", title: "Status"),
        .init(key: "expires_at", title: "Expires"),
      ])
  }
  @ViewBuilder private func access(_ value: JSONValue) -> some View {
    let summary = value["summary"]
    Metrics(items: [
      ("Total users", summary["total_users"].string),
      ("Active users", summary["active_users_window"].string),
      ("Open sessions", summary["open_sessions"].string),
      ("Locked users", summary["locked_users_now"].string),
      ("Failed logins", summary["failed_logins_window"].string),
    ])
    usage(
      "Workspace usage", value["section_usage"].records, label: "section_name", color: Theme.teal)
    usage("User activity", value["user_usage"].records, label: "email", color: Theme.violet)
    table(
      "Security events", value["recent_security_events"].records,
      [
        .init(key: "created_at", title: "Time"), .init(key: "email", title: "User"),
        .init(key: "event_type", title: "Event"), .init(key: "event_category", title: "Category"),
        .init(key: "section_name", title: "Workspace"), .init(key: "ip_address", title: "IP"),
      ])
    table(
      "Active sessions", value["active_sessions"].records,
      [
        .init(key: "email", title: "User"), .init(key: "last_seen_at", title: "Last seen"),
        .init(key: "ip_address", title: "IP"), .init(key: "user_agent", title: "Client"),
      ])
    table(
      "Access IPs", value["access_ips"].records,
      [
        .init(key: "ip_address", title: "IP"),
        .init(key: "event_count", title: "Events", style: .number),
        .init(key: "last_seen_at", title: "Last seen"),
      ])
  }
  @ViewBuilder private func models(_ value: JSONValue) -> some View {
    let runtime = value["runtime"]
    Metrics(items: [
      ("Provider", runtime["provider"].string), ("Model", runtime["model"].string),
      ("Temperature", runtime["temperature"].string),
      ("Prompts", String(value["prompts"].records.count)),
    ])
    if let record = runtime.dictionaryValue { Panel("Runtime") { RecordDetails(record: record) } }
    table(
      "Tuning parameters", value["parameters"].records,
      [
        .init(key: "group", title: "Group"), .init(key: "name", title: "Parameter"),
        .init(key: "value", title: "Active"), .init(key: "default", title: "Default"),
        .init(key: "description", title: "Description"),
      ])
    Panel("Prompts") {
      ForEach(Array(value["prompts"].records.enumerated()), id: \.offset) { _, prompt in
        DisclosureGroup(prompt.text("name")) {
          Text(prompt.text("file")).font(.caption2).foregroundStyle(.secondary)
          Text(prompt.text("prompt")).font(.system(.caption, design: .monospaced)).textSelection(
            .enabled)
        }
      }
    }
  }
  @ViewBuilder private func system(_ value: JSONValue) -> some View {
    Metrics(items: [
      (
        "Failed jobs",
        String(
          value["job_status"].records.filter { $0.text("status").lowercased() == "failed" }.count)
      ),
      (
        "Running jobs",
        String(
          value["job_status"].records.filter { $0.text("status").lowercased() == "running" }.count)
      ), ("Datasets", String(value["dataset_status"].records.count)),
      (
        "Connector failures",
        Format.number(
          value["connector_calls"].records.reduce(0) { $0 + ($1.number("failure_count") ?? 0) })
      ),
    ])
    table("Job status", value["job_status"].records, jobColumns)
    table("Dataset freshness", value["dataset_status"].records, datasetColumns)
    table(
      "External calls", value["connector_calls"].records,
      [
        .init(key: "connector", title: "Connector"), .init(key: "operation", title: "Operation"),
        .init(key: "call_count", title: "Calls", style: .number),
        .init(key: "failure_count", title: "Failures", style: .number),
        .init(key: "p95_duration_ms", title: "P95 ms", style: .number),
      ])
    table(
      "LLM calls", value["model_calls"].records,
      [
        .init(key: "provider", title: "Provider"), .init(key: "model", title: "Model"),
        .init(key: "purpose", title: "Purpose"),
        .init(key: "call_count", title: "Calls", style: .number),
        .init(key: "failure_count", title: "Failures", style: .number),
      ])
  }
  private func usage(_ title: String, _ rows: [Record], label: String, color: Color) -> some View {
    Panel(title) {
      Chart(Array(rows.enumerated()), id: \.offset) { _, row in
        if let count = row.number("event_count") {
          BarMark(x: .value("Events", count), y: .value("Name", row.text(label))).foregroundStyle(
            color)
        }
      }.frame(height: CGFloat(max(150, rows.count * 28)))
    }
  }
  private func table(_ title: String, _ rows: [Record], _ columns: [WorkbookColumn]) -> some View {
    Panel(title) { WorkbookTable(rows: rows, columns: columns) }
  }
  private func validEmail(_ value: String) -> Bool {
    value.trimmingCharacters(in: .whitespacesAndNewlines).range(
      of: "^\\S+@\\S+\\.\\S+$", options: .regularExpression) != nil
  }
  private func load() async {
    await resource.load(key: String(days)) {
      try await session.api.get("/api/admin", query: ["days": String(days)])
    }
  }
  private func showResult(_ result: JSONValue, link: String, sent: String, created: String) {
    notice =
      result["email_sent"] == .bool(true)
      ? sent : result["message"].string.isEmpty ? created : result["message"].string
    if let url = URL(string: result[link].string), ["http", "https"].contains(url.scheme ?? "") {
      issuedURL = url
    }
  }
  private func issueInvite() async {
    working = true
    actionError = ""
    notice = ""
    issuedURL = nil
    defer { working = false }
    do {
      let result: JSONValue = try await session.api.post(
        "/api/admin/invites",
        body: [
          "email": .string(email), "role": .string(role),
          "share_fraction": .number(role == "investor" ? (Double(share) ?? 0) / 100 : 0),
        ])
      showResult(result, link: "invite_url", sent: "Invite sent.", created: "Invite created.")
      email = ""
      await load()
    } catch { actionError = error.localizedDescription }
  }
  private func issueReset() async {
    working = true
    actionError = ""
    notice = ""
    issuedURL = nil
    defer { working = false }
    do {
      let result: JSONValue = try await session.api.post(
        "/api/admin/password-resets", body: ["email": .string(resetEmail)])
      showResult(
        result, link: "reset_url", sent: "Password reset sent.", created: "Password reset issued.")
      resetEmail = ""
    } catch { actionError = error.localizedDescription }
  }
}
