import Combine
import Foundation

@MainActor
final class AppSession: ObservableObject {
  @Published var context: UserContext?
  @Published var restoring = true
  @Published var showingLogin = false
  @Published var error = ""
  @Published var revision = UUID()
  @Published var accountLink: AccountLink?
  @Published var accountLinkError = ""
  let api = APIClient()
  private var generation = 0
  private var changingSession = false
  var authenticated: Bool { context != nil }

  func restore() async {
    guard !changingSession else { return }
    generation += 1
    let request = generation
    do {
      let response: SessionResponse = try await api.get("/api/auth/session")
      guard request == generation, !Task.isCancelled else { return }
      if context != nil && !response.authenticated { revision = UUID() }
      context = response.authenticated ? response.context : nil
      error = ""
    } catch {
      if request == generation && !Task.isCancelled { self.error = error.localizedDescription }
    }
    if request == generation { restoring = false }
  }

  func signIn(email: String, password: String) async -> Bool {
    guard !changingSession else { return false }
    changingSession = true
    defer {
      changingSession = false
      restoring = false
    }
    generation += 1
    let request = generation
    do {
      let response: SessionResponse = try await api.post(
        "/api/auth/login",
        body: [
          "email": .string(email.trimmingCharacters(in: .whitespacesAndNewlines)),
          "password": .string(password),
        ])
      guard response.authenticated, let context = response.context else {
        throw APIError.invalidResponse
      }
      guard request == generation, !Task.isCancelled else { return false }
      self.context = context
      error = ""
      revision = UUID()
      showingLogin = false
      restoring = false
      return true
    } catch {
      if request == generation { self.error = error.localizedDescription }
      return false
    }
  }

  func signOut() async {
    guard !changingSession else { return }
    changingSession = true
    defer { changingSession = false }
    generation += 1
    do {
      let _: JSONValue = try await api.post("/api/auth/logout")
    } catch { /* Always clear this device, including when offline. */  }
    await api.clearSession()
    expire()
  }

  func expire() {
    generation += 1
    context = nil
    restoring = false
    revision = UUID()
  }

  func handleSessionExpiry() {
    // A queued read can finish while a new login is replacing the old session.
    // The credential operation's response owns that transition.
    guard !changingSession else { return }
    expire()
  }

  func openAccountLink(_ url: URL) {
    do {
      accountLink = try AccountLink(url: url)
      error = ""
      showingLogin = true
    } catch { accountLinkError = error.localizedDescription }
  }

  func activate(body: Record) async throws {
    guard !changingSession else { throw CancellationError() }
    changingSession = true
    defer {
      changingSession = false
      restoring = false
    }
    generation += 1
    let request = generation
    let _: JSONValue = try await api.post("/api/auth/account/invite", body: body)
    let response: SessionResponse = try await api.get("/api/auth/session")
    guard response.authenticated, let context = response.context else {
      throw APIError.invalidResponse
    }
    guard request == generation, !Task.isCancelled else { throw CancellationError() }
    self.context = context
    revision = UUID()
    accountLink = nil
    showingLogin = false
    restoring = false
  }
}
