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
    @Published var accessToken: String?
    @Published var refreshToken: String?
    @Published var userContext: [String: JSONValue]?
    @Published var authStatus: AuthStatusResponse?
    @Published var errorMessage: String = ""

    private let defaults = UserDefaults.standard
    private let baseURLDefaultsKey = "sn.api.base_url"
    private let accessTokenStore = KeychainTokenStore(
        service: "com.torrescapital.spectralnaturemvp",
        account: "access_token"
    )
    private let refreshTokenStore = KeychainTokenStore(
        service: "com.torrescapital.spectralnaturemvp",
        account: "refresh_token"
    )

    init() {
        let storedBaseURL = defaults.string(forKey: baseURLDefaultsKey) ?? AppEnvironment.defaultBaseURL
        baseURLText = storedBaseURL
        accessToken = accessTokenStore.readToken()
        refreshToken = refreshTokenStore.readToken()
        mode = (accessToken?.isEmpty == false) ? .authenticated : .unauthenticated
    }

    var resolvedBaseURL: URL {
        if let url = URL(string: baseURLText), url.scheme != nil {
            return url
        }
        return URL(string: AppEnvironment.fallbackBaseURL)!
    }

    var apiClient: APIClient {
        APIClient(baseURL: resolvedBaseURL, bearerToken: accessToken)
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
            accessToken = response.accessToken
            refreshToken = response.refreshToken
            accessTokenStore.saveToken(response.accessToken)
            refreshTokenStore.saveToken(response.refreshToken)
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
        if accessToken != nil {
            _ = try? await apiClient.logout(refreshToken: refreshToken ?? "")
        }
        accessToken = nil
        refreshToken = nil
        userContext = nil
        accessTokenStore.clearToken()
        refreshTokenStore.clearToken()
        mode = .unauthenticated
    }
}
