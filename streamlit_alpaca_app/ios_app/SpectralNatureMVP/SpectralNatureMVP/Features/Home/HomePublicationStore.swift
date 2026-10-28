import Combine
import CryptoKit
import Foundation

/// Public publications outlive a screen, a login transition, and an app process.
@MainActor
final class HomePublicationStore: ObservableObject {
  @Published private var value: HomeSnapshot?
  @Published private var target = ""
  @Published private(set) var loading = false
  @Published private(set) var error = ""
  private let api: APIClient
  private let cache: HomePublicationCache
  private var restored: Task<HomePublication?, Never>?
  private var latest: HomePublication?
  private var pending: HomePublication?
  private var etag = ""
  private var request: Task<Void, Never>?
  private var requestID: UUID?

  init(api: APIClient, directory: URL? = nil) {
    self.api = api
    let origin = api.baseURL.absoluteString
    let directory = directory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
      .appendingPathComponent("PublicHome", isDirectory: true)
    let key = SHA256.hash(data: Data(origin.utf8)).map { String(format: "%02x", $0) }.joined()
    let cache = HomePublicationCache(url: directory.appendingPathComponent(key + ".json"))
    self.cache = cache
    restored = Task { await cache.read() }
  }

  func snapshot(for date: String) -> HomeSnapshot? {
    date == target ? value : date.isEmpty ? latest?.snapshot : nil
  }

  /// Coalesce readers, but never join a cancelled request after foreground return.
  func refresh(date: String, quiet: Bool, canApply: @escaping @MainActor () -> Bool) async {
    if target != date {
      request?.cancel()
      request = nil
      requestID = nil
      pending = nil
      target = date
      value = date.isEmpty ? latest?.snapshot : nil
      etag = date.isEmpty ? latest?.etag ?? "" : ""
      error = ""
    }
    if let request, !request.isCancelled {
      await request.value
      return
    }
    let identity = UUID()
    requestID = identity
    let operation = Task { await self.fetch(date: date, identity: identity, quiet: quiet, canApply: canApply) }
    request = operation
    await withTaskCancellationHandler {
      await operation.value
    } onCancel: {
      operation.cancel()
    }
  }

  func applyPending(when ready: Bool) {
    guard ready, let pending else { return }
    value = pending.snapshot
    self.pending = nil
  }

  private func fetch(date: String, identity: UUID, quiet: Bool, canApply: @MainActor () -> Bool) async {
    loading = !quiet || value == nil
    error = ""
    defer {
      if requestID == identity {
        request = nil
        requestID = nil
        loading = false
      }
    }
    if let restored {
      let saved = await restored.value
      self.restored = nil
      if latest == nil { latest = saved }
      guard !Task.isCancelled, requestID == identity, target == date else { return }
      if date.isEmpty, value == nil, let latest {
        value = latest.snapshot
        etag = latest.etag
        if quiet { loading = false }
      }
    }
    do {
      try Task.checkCancellation()
      let result: (value: HomeSnapshot?, etag: String) = try await api.getIfChanged(
        "/api/home", query: date.isEmpty ? [:] : ["date": date], etag: etag)
      guard !Task.isCancelled, requestID == identity, target == date else { return }
      if let home = result.value {
        guard home.status == "available", home.isReplay == !date.isEmpty,
          date.isEmpty || home.viewDate == date else {
          if value == nil { error = "Daily Brief unavailable" }
          return
        }
        if etag.isEmpty || result.etag.isEmpty || etag != result.etag {
          let publication = HomePublication(snapshot: home, etag: result.etag)
          pending = publication
          etag = result.etag
          applyPending(when: canApply())
          if date.isEmpty {
            latest = publication
            await cache.write(publication)
          }
        }
      } else {
        applyPending(when: canApply())
      }
    } catch {
      guard !Task.isCancelled, requestID == identity else { return }
      if !quiet || value == nil { self.error = error.localizedDescription }
    }
  }
}

private struct HomePublication: Codable {
  let snapshot: HomeSnapshot
  let etag: String
}

/// Only the public live DTO is persisted, scoped to its gateway. No credentials,
/// replay responses or private workspace data enter this file.
private actor HomePublicationCache {
  let url: URL
  init(url: URL) { self.url = url }

  func read() -> HomePublication? {
    guard let bytes = try? Data(contentsOf: url),
      let publication = try? JSONDecoder().decode(HomePublication.self, from: bytes),
      publication.snapshot.status == "available", !publication.snapshot.isReplay else { return nil }
    return publication
  }

  func write(_ publication: HomePublication) {
    do {
      try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
      try JSONEncoder().encode(publication).write(to: url, options: .atomic)
      var file = url
      var attributes = URLResourceValues()
      attributes.isExcludedFromBackup = true
      try file.setResourceValues(attributes)
    } catch {
      // A full/unavailable disk must not discard a successfully loaded Brief.
    }
  }
}
