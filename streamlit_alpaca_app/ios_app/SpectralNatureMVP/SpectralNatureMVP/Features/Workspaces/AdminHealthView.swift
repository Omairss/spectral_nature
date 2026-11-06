import SwiftUI

struct AdminHealthView: View {
  @EnvironmentObject private var session: AppSession
  @StateObject private var health = Resource<JSONValue>()
  @State private var hours = 24
  @State private var resource = ""
  @Binding var refresh: Int

  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      HStack {
        Picker("Window", selection: $hours) {
          Text("1 hour").tag(1)
          Text("24 hours").tag(24)
          Text("3 days").tag(72)
          Text("7 days").tag(168)
        }
        Spacer()
        Button("Refresh") { refresh += 1 }.disabled(health.loading)
      }
      ResourceStatus(resource: health) { refresh += 1 }
      if let value = health.value { contents(value) }
    }
    .task(id: "\(hours)|\(resource)|\(refresh)") { await load() }
  }

  @ViewBuilder private func contents(_ value: JSONValue) -> some View {
    let database = value["database"]
    let attempts = database["attempts"].records
    Panel("Health") {
      if !value["scope"].string.isEmpty { Text(value["scope"].string).font(.caption) }
      observed(database["observed_at"].string)
      if !database["receipt_source"].string.isEmpty {
        Text("Receipts: \(database["receipt_source"].string)").font(.caption)
      }
      unavailable(database["unavailable"])
      Metrics(items: [
        ("Recorded HTTP attempts", total(attempts, "attempts")),
        ("Failed attempts", total(attempts, "failures")),
        ("Input tokens", total(attempts, "input_tokens")),
        ("Output tokens", total(attempts, "output_tokens")),
        ("Cached input tokens", total(attempts, "cached_tokens")),
        ("Usage unknown", total(attempts, "unknown_usage")),
        ("Known cost estimate (USD, upper)", total(attempts, "estimated_usd_max")),
        ("Unpriced attempts", total(attempts, "unpriced_attempts")),
      ])
    }
    if let maintenance = value["maintenance"].dictionaryValue {
      Panel("Isolated dev maintenance") {
        unavailable(maintenance["unavailable"] ?? .null)
        WorkbookTable(
          rows: maintenance.rows("reports"),
          columns: [
            .init(key: "created_at_utc", title: "Started", style: .dateTime),
            .init(key: "research_status", title: "Research"),
            .init(key: "edits", title: "Edits", style: .number),
            .init(key: "run_id", title: "Run"),
          ])
        WorkbookTable(
          rows: maintenance.rows("reports").flatMap { report in
            report.rows("articles").map { article in
              article.merging(["run_id": report["run_id"] ?? .null]) { _, run in run }
            }
          },
          columns: [
            .init(key: "page_id", title: "Article"), .init(key: "status", title: "Result"),
            .init(key: "error", title: "Failure"),
          ])
        WorkbookTable(
          rows: maintenance.rows("attempts"),
          columns: [
            .init(key: "model", title: "Model"), .init(key: "purpose", title: "Purpose"),
          ] + attemptCounts + [
            .init(key: "estimated_usd_max", title: "Known cost (USD, upper)", style: .number)
          ])
      }
    }
    Panel("Published outputs") {
      unavailable(value["outputs_unavailable"])
      WorkbookTable(
        rows: value["outputs"].records,
        columns: [
          .init(key: "dataset", title: "Dataset"),
          .init(key: "asof", title: "As of", style: .dateTime),
          .init(key: "published_at", title: "Published", style: .dateTime),
          .init(key: "rows", title: "Rows", style: .number),
          .init(key: "version", title: "Version"),
          .init(key: "status", title: "Unavailable"),
        ])
    }
    if !database["usage_trend"].records.isEmpty {
      Panel("LLM tokens per hour") {
        TimeSeriesChart(
          rows: database["usage_trend"].records, date: "time",
          series: [("input_tokens", "Input"), ("output_tokens", "Output")],
          intraday: hours <= 24, fullSelectionTimestamp: true)
      }
    }
    resourceUsage(value)
    table(
      "Provider attempts", attempts,
      [
        .init(key: "model", title: "Model"), .init(key: "surface", title: "Surface"),
        .init(key: "purpose", title: "Purpose"),
        .init(key: "operations", title: "Operations", style: .number),
      ] + attemptCounts + [
        .init(key: "unfinished", title: "Unfinished", style: .number),
        .init(key: "peak_input_tokens", title: "Peak input tokens", style: .number),
        .init(key: "reasoning_tokens", title: "Reasoning tokens", style: .number),
        .init(key: "cached_tokens", title: "Cached tokens", style: .number),
        .init(key: "estimated_usd_max", title: "Known cost estimate (USD, upper)", style: .number),
        .init(key: "p95_ms", title: "P95 ms", style: .number),
      ])
    Panel("Pipeline database connections · shared server") {
      Metrics(
        items: database["capacity"].records.map {
          (
            $0.text("name").replacingOccurrences(of: "_", with: " "),
            Format.number($0.number("value"))
          )
        })
      WorkbookTable(
        rows: database["connections"].records,
        columns: [
          .init(key: "database", title: "Database"),
          .init(key: "application", title: "Application"),
          .init(key: "state", title: "State"), .init(key: "wait_event_type", title: "Wait"),
          .init(key: "connections", title: "Connections", style: .number),
          .init(key: "oldest_transaction_seconds", title: "Oldest transaction (s)", style: .number),
        ])
    }
    table(
      "Learning episodes · shared store", database["learning"].records,
      [
        .init(key: "status", title: "State"),
        .init(key: "episodes", title: "Episodes", style: .number),
        .init(key: "updated_at", title: "Updated", style: .dateTime),
      ])
    table(
      "Latest executions", database["components"].records,
      [
        .init(key: "job_name", title: "Job"), .init(key: "status", title: "State"),
        .init(key: "progress_stage", title: "Stage"),
        .init(key: "progress_message", title: "Result"),
        .init(key: "start_time_utc", title: "Started", style: .dateTime),
        .init(key: "end_time_utc", title: "Ended", style: .dateTime),
        .init(key: "platform_execution_name", title: "Execution"),
      ])
  }

  @ViewBuilder private func resourceUsage(_ value: JSONValue) -> some View {
    let metrics = value["metrics"]
    Panel("Resource usage") {
      Picker("Resource", selection: $resource) {
        Text("Select resource").tag("")
        ForEach(Array(value["resources"].records.enumerated()), id: \.offset) { _, item in
          Text(item.text("name") + (item.text("scope") == "shared" ? " · shared" : ""))
            .tag(item.text("name"))
        }
      }
      unavailable(value["cloud_unavailable"])
      unavailable(metrics["unavailable"])
      if !metrics["resource"].string.isEmpty { Text(metrics["resource"].string).font(.caption) }
      observed(metrics["observed_at"].string)
      unavailable(metrics["allocation_unavailable"])
      ForEach(Array(metrics["allocation"]["containers"].records.enumerated()), id: \.offset) {
        _, item in
        Text(
          "\(item.text("name")) · \(Format.number(item.number("cpu"))) CPU · \(item.text("memory")) per replica"
        )
        .font(.caption).foregroundStyle(.secondary)
      }
      ForEach(Array(metrics["series"].records.enumerated()), id: \.offset) { _, series in
        let metric = series.text("metric")
        let scale = metric.contains("Bytes") ? 1_048_576.0 : metric == "UsageNanoCores" ? 1e9 : 1
        let points = series.rows("points").map { point in
          point.merging([
            "average": point.number("average").map { .number($0 / scale) } ?? .null,
            "maximum": point.number("maximum").map { .number($0 / scale) } ?? .null,
          ]) { _, scaled in scaled }
        }
        VStack(alignment: .leading, spacing: 10) {
          Text(metricTitle(metric, unit: series.text("unit"))).font(.subheadline.weight(.semibold))
          TimeSeriesChart(
            rows: points, date: "time", series: [("average", "Average"), ("maximum", "Peak")],
            intraday: hours <= 24, includesZero: true, fullSelectionTimestamp: true)
        }
      }
    }
  }

  private var attemptCounts: [WorkbookColumn] {
    [
      .init(key: "attempts", title: "Attempts", style: .number),
      .init(key: "failures", title: "Failures", style: .number),
      .init(key: "input_tokens", title: "Input tokens", style: .number),
      .init(key: "output_tokens", title: "Output tokens", style: .number),
      .init(key: "unknown_usage", title: "Unknown usage", style: .number),
      .init(key: "unpriced_attempts", title: "Unpriced attempts", style: .number),
    ]
  }
  private func total(_ rows: [Record], _ key: String) -> String {
    let values = rows.compactMap { $0.number(key) }
    return Format.number(values.isEmpty ? nil : values.reduce(0, +))
  }
  private func metricTitle(_ metric: String, unit: String) -> String {
    let names = [
      "cpu_percent": "Database CPU (%)", "memory_percent": "Database memory (%)",
      "active_connections": "Server connections", "connections_failed": "Rejected connections",
      "storage_percent": "Storage (%)", "UsageNanoCores": "CPU · resource total (cores)",
      "UsageBytes": "Memory · resource total (MiB)",
      "WorkingSetBytes": "Memory · resource total (MiB)",
      "Replicas": "Replicas", "RestartCount": "Restarts",
    ]
    return names[metric] ?? metric + (unit.isEmpty ? "" : " (\(unit))")
  }
  @ViewBuilder private func observed(_ value: String) -> some View {
    if let date = Format.date(value) {
      Text("Observed \(date.formatted(date: .abbreviated, time: .standard))")
        .font(.caption).foregroundStyle(.secondary)
    }
  }
  @ViewBuilder private func unavailable(_ value: JSONValue) -> some View {
    if let fields = value.dictionaryValue {
      ForEach(fields.keys.sorted(), id: \.self) { key in
        Failure(
          message: "\(key.replacingOccurrences(of: "_", with: " ")): \(fields[key]?.string ?? "")")
      }
    } else if !value.string.isEmpty {
      Failure(message: value.string)
    }
  }
  private func table(_ title: String, _ rows: [Record], _ columns: [WorkbookColumn]) -> some View {
    Panel(title) { WorkbookTable(rows: rows, columns: columns) }
  }
  private func load() async {
    let selectedHours = hours
    let selectedResource = resource
    await health.load(key: "\(selectedHours)|\(selectedResource)", clearOnFailure: true) {
      let value: JSONValue = try await session.api.get(
        "/api/admin/health",
        query: [
          "hours": String(selectedHours), "resource": selectedResource,
        ])
      return value
    }
    guard !Task.isCancelled, resource.isEmpty, let value = health.value else { return }
    let resources = value["resources"].records
    resource =
      resources.first { $0.text("name") == value["database"]["server_name"].string }?.text("name")
      ?? resources.first { $0.text("kind").contains("PostgreSQL") }?.text("name")
      ?? resources.first?.text("name") ?? ""
  }
}
