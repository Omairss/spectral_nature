import Foundation

enum APIError: LocalizedError {
    case invalidResponse
    case invalidServerURL
    case server(status: Int, message: String)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "The server returned an invalid response."
        case .invalidServerURL:
            return "The API base URL is invalid."
        case let .server(status, message):
            return "Server error (\(status)): \(message)"
        case let .decoding(error):
            return "Response decode error: \(error.localizedDescription)"
        case let .transport(error):
            return "Network error: \(error.localizedDescription)"
        }
    }
}

final class APIClient {
    private let baseURL: URL
    private let bearerToken: String?
    private let session: URLSession

    init(baseURL: URL, bearerToken: String? = nil, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.bearerToken = bearerToken
        self.session = session
    }

    func authStatus() async throws -> AuthStatusResponse {
        try await request(path: "/v1/auth/status", method: "GET")
    }

    func login(email: String, password: String) async throws -> LoginResponseBody {
        try await request(
            path: "/v1/auth/login",
            method: "POST",
            body: LoginRequestBody(email: email, password: password)
        )
    }

    func refresh(refreshToken: String, rotateRefreshToken: Bool = true) async throws -> LoginResponseBody {
        try await request(
            path: "/v1/auth/refresh",
            method: "POST",
            body: RefreshRequestBody(refreshToken: refreshToken, rotateRefreshToken: rotateRefreshToken)
        )
    }

    func logout(refreshToken: String = "") async throws -> BasicResponse {
        try await request(
            path: "/v1/auth/logout",
            method: "POST",
            body: LogoutRequestBody(refreshToken: refreshToken),
            requiresAuth: true
        )
    }

    func me() async throws -> MeResponse {
        try await request(path: "/v1/me", method: "GET")
    }

    func query(_ body: QueryRequestBody) async throws -> QueryResponseBody {
        try await request(path: "/v1/query", method: "POST", body: body)
    }

    func fetchCapabilities() async throws -> QueryResponseBody {
        try await request(path: "/v1/capabilities", method: "GET")
    }

    func fetchDataset(name: String, params: [String: JSONValue]) async throws -> QueryResponseBody {
        try await request(
            path: "/v1/dataset/\(name)",
            method: "POST",
            body: QueryBody(params: params)
        )
    }

    func fetchChart(name: String, params: [String: JSONValue]) async throws -> QueryResponseBody {
        try await request(
            path: "/v1/chart/\(name)",
            method: "POST",
            body: QueryBody(params: params)
        )
    }

    private func request<Response: Decodable>(
        path: String,
        method: String,
        requiresAuth: Bool = false
    ) async throws -> Response {
        try await execute(path: path, method: method, body: nil, requiresAuth: requiresAuth)
    }

    private func request<Response: Decodable, Body: Encodable>(
        path: String,
        method: String,
        body: Body,
        requiresAuth: Bool = false
    ) async throws -> Response {
        try await execute(path: path, method: method, body: body, requiresAuth: requiresAuth)
    }

    private func execute<Response: Decodable, Body: Encodable>(
        path: String,
        method: String,
        body: Body?,
        requiresAuth: Bool
    ) async throws -> Response {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidServerURL
        }
        let normalizedPath = path.hasPrefix("/") ? path : "/\(path)"
        let basePath = components.path == "/" ? "" : components.path
        components.path = "\(basePath)\(normalizedPath)"
        guard let url = components.url else {
            throw APIError.invalidServerURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = bearerToken, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } else if requiresAuth {
            throw APIError.server(status: 401, message: "Missing session token.")
        }

        if let body {
            request.httpBody = try JSONEncoder().encode(body)
        }

        let payload: Data
        let response: URLResponse
        do {
            (payload, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        guard (200 ..< 300).contains(httpResponse.statusCode) else {
            let message = decodeErrorMessage(data: payload) ?? "Request failed."
            throw APIError.server(status: httpResponse.statusCode, message: message)
        }

        do {
            return try JSONDecoder().decode(Response.self, from: payload)
        } catch {
            throw APIError.decoding(error)
        }
    }

    private func decodeErrorMessage(data: Data) -> String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return nil
        }
        if let detail = object["detail"] as? String, !detail.isEmpty {
            return detail
        }
        if let error = object["error"] as? String, !error.isEmpty {
            return error
        }
        return nil
    }
}

private struct QueryBody: Encodable {
    let params: [String: JSONValue]
}

private struct RefreshRequestBody: Encodable {
    let refreshToken: String
    let rotateRefreshToken: Bool

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
        case rotateRefreshToken = "rotate_refresh_token"
    }
}

private struct LogoutRequestBody: Encodable {
    let refreshToken: String

    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}
