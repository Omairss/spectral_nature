import Foundation
import Combine

@MainActor
final class AppSession: ObservableObject {
    enum Mode {
        case unauthenticated
        case authenticated
        case guest
    }

    @Published var baseURLText: String
    @Published var mode: Mode
    @Published var sessionToken: String?
    @Published var userContext: [String: JSONValue]?
    @Published var authStatus: AuthStatusResponse?
    @Published var errorMessage: String = ""

    private let defaults = UserDefaults.standard
    private let baseURLDefaultsKey = "sn.api.base_url"
    private let tokenStore = KeychainTokenStore(
        service: "com.torrescapital.spectralnaturemvp",
        account: "session_token"
    )

    init() {
        let storedBaseURL = defaults.string(forKey: baseURLDefaultsKey) ?? AppEnvironment.defaultBaseURL
        baseURLText = storedBaseURL
        sessionToken = tokenStore.readToken()
        mode = (sessionToken?.isEmpty == false) ? .authenticated : .unauthenticated
    }

    var resolvedBaseURL: URL {
        if let url = URL(string: baseURLText), url.scheme != nil {
            return url
        }
        return URL(string: AppEnvironment.fallbackBaseURL)!
    }

    var apiClient: APIClient {
        APIClient(baseURL: resolvedBaseURL, bearerToken: sessionToken)
    }

    func setBaseURL(_ value: String) {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        baseURLText = trimmed
        defaults.set(trimmed, forKey: baseURLDefaultsKey)
        errorMessage = ""
    }

    func refreshAuthStatus() async {
        do {
            let status = try await apiClient.authStatus()
            authStatus = status
            errorMessage = ""
        } catch {
            errorMessage = "Auth status check failed: \(error.localizedDescription)"
        }
    }

    func signIn(email: String, password: String) async {
        do {
            let response = try await apiClient.login(email: email, password: password)
            sessionToken = response.sessionToken
            tokenStore.saveToken(response.sessionToken)
            userContext = response.context
            mode = .authenticated
            errorMessage = ""
        } catch {
            errorMessage = "Sign in failed: \(error.localizedDescription)"
        }
    }

    func continueAsGuest() {
        userContext = nil
        if mode == .unauthenticated {
            mode = .guest
        }
        errorMessage = ""
    }

    func signOut() async {
        if sessionToken != nil {
            _ = try? await apiClient.logout()
        }
        sessionToken = nil
        userContext = nil
        tokenStore.clearToken()
        mode = .unauthenticated
    }
}
