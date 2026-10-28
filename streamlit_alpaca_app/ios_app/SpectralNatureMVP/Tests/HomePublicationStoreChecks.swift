// Standalone lifecycle regression probe. Compile with the production store,
// APIModels.swift and JSONValue.swift; this transport replaces APIClient only.
import Foundation

actor APIClient {
  nonisolated let baseURL: URL
  enum Reply { case body(Data, String), unchanged, failed, blocked }
  private var replies: [Reply] = []
  private var reads: [(String, [String: String], String)] = []
  private var waiters: [(Int, CheckedContinuation<Void, Never>)] = []
  init(origin: String = "https://native-brief.example") { baseURL = URL(string: origin)! }
  func enqueue(_ reply: Reply) { replies.append(reply) }
  func waitForReads(_ count: Int) async {
    if reads.count >= count { return }
    await withCheckedContinuation { waiters.append((count, $0)) }
  }
  func headers() -> [String] { reads.map(\.2) }
  func getIfChanged<T: Decodable>(_ path: String, query: [String: String], etag: String) async throws -> (value: T?, etag: String) {
    reads.append((path, query, etag))
    for (count, continuation) in waiters where count <= reads.count { continuation.resume() }
    waiters.removeAll { $0.0 <= reads.count }
    guard !replies.isEmpty else { preconditionFailure("Unexpected request") }
    switch replies.removeFirst() {
    case let .body(data, tag):
      let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
      return (try decoder.decode(T.self, from: data), tag)
    case .unchanged: return (nil, etag)
    case .failed: throw URLError(.notConnectedToInternet)
    case .blocked:
      try await Task.sleep(for: .seconds(60))
      throw URLError(.timedOut)
    }
  }
}

@main
struct HomePublicationStoreChecks {
  @MainActor static func main() async throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent("home-retention-\(UUID())")
    defer { try? FileManager.default.removeItem(at: folder) }
    let original = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
    let raw = try JSONSerialization.jsonObject(with: original) as! [String: Any]
    func publication(_ id: String, date: String = "", status: String = "available") throws -> Data {
      var next = raw
      next["run_id"] = id; next["view_date"] = date; next["is_replay"] = !date.isEmpty; next["status"] = status
      return try JSONSerialization.data(withJSONObject: next)
    }
    let api = APIClient()
    let store = HomePublicationStore(api: api, directory: folder)
    await api.enqueue(.body(try publication("one"), "one"))
    await store.refresh(date: "", quiet: true) { true }
    precondition(store.snapshot(for: "")?.runId == "one")
    let graph = store.snapshot(for: "")?.graph
    let beats = store.snapshot(for: "")?.beats.count
    let highlights = store.snapshot(for: "")?.dailyDatapoints?.count

    // A new app owner restores the complete DTO before the network returns.
    let nextAPI = APIClient()
    let reopened = HomePublicationStore(api: nextAPI, directory: folder)
    await nextAPI.enqueue(.blocked)
    let oldRead = Task { await reopened.refresh(date: "", quiet: true) { true } }
    await nextAPI.waitForReads(1)
    precondition(reopened.snapshot(for: "")?.runId == "one")
    precondition(reopened.snapshot(for: "")?.graph == graph)
    precondition(reopened.snapshot(for: "")?.beats.count == beats)
    precondition(reopened.snapshot(for: "")?.dailyDatapoints?.count == highlights)
    precondition(!reopened.loading)
    let restoredHeaders = await nextAPI.headers()
    precondition(restoredHeaders == ["one"])
    print("PASS: full publication restored before delayed network, with ETag")

    // Resume immediately, while the cancelled read is still unwinding.
    oldRead.cancel()
    await nextAPI.enqueue(.body(try publication("two"), "two"))
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "two")
    await oldRead.value
    print("PASS: foreground return replaces a cancelled request immediately")

    await nextAPI.enqueue(.failed)
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "two" && reopened.error.isEmpty)
    await nextAPI.enqueue(.body(try publication("unavailable", status: "unavailable"), "bad"))
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "two")
    print("PASS: failures and unavailable publications retain the last good Brief")

    await nextAPI.enqueue(.body(try publication("three"), "three"))
    await reopened.refresh(date: "", quiet: true) { false }
    precondition(reopened.snapshot(for: "")?.runId == "two")
    await nextAPI.enqueue(.unchanged)
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "three")
    print("PASS: audio deferral retains a pending publication through 304")

    await nextAPI.enqueue(.body(try publication("replay", date: "2026-10-21"), "replay"))
    await reopened.refresh(date: "2026-10-21", quiet: true) { true }
    precondition(reopened.snapshot(for: "2026-10-21")?.runId == "replay")
    precondition(reopened.snapshot(for: "")?.runId == "three")
    precondition(reopened.snapshot(for: "2026-10-20") == nil)
    await nextAPI.enqueue(.unchanged)
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "three")
    let replayHeaders = await nextAPI.headers()
    precondition(replayHeaders.last == "three")
    print("PASS: replay and live identities stay separate")

    await nextAPI.enqueue(.body(try publication("four"), ""))
    await reopened.refresh(date: "", quiet: true) { true }
    await nextAPI.enqueue(.body(try publication("five"), ""))
    await reopened.refresh(date: "", quiet: true) { true }
    precondition(reopened.snapshot(for: "")?.runId == "five")
    print("PASS: missing ETags do not freeze publication changes")

    let otherAPI = APIClient(origin: "https://other-native-brief.example")
    let other = HomePublicationStore(api: otherAPI, directory: folder)
    await otherAPI.enqueue(.failed)
    await other.refresh(date: "", quiet: true) { true }
    precondition(other.snapshot(for: "") == nil && !other.error.isEmpty && !other.loading)
    let otherHeaders = await otherAPI.headers()
    precondition(otherHeaders == [""])
    print("PASS: gateways never share cached publications; empty failure has a retry state")

    let cacheFiles = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
    precondition(cacheFiles.count == 1)
    try Data("invalid cache".utf8).write(to: cacheFiles[0], options: .atomic)
    let recovered = HomePublicationStore(api: api, directory: folder)
    await api.enqueue(.body(try publication("recovered"), "recovered"))
    await recovered.refresh(date: "", quiet: true) { true }
    precondition(recovered.snapshot(for: "")?.runId == "recovered")
    print("PASS: unreadable local cache recovers through the public gateway")
  }
}
