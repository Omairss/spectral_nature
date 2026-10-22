import Foundation

enum APIError: LocalizedError {
  case server(Int, String)
  case invalidResponse
  case fileTooLarge
  var errorDescription: String? {
    switch self {
    case let .server(_, message): return message
    case .invalidResponse: return "The response could not be read."
    case .fileTooLarge: return "Choose a source file smaller than 5 MB."
    }
  }
}

private final class RedirectPolicy: NSObject, URLSessionTaskDelegate {
  func urlSession(
    _ session: URLSession, task: URLSessionTask,
    willPerformHTTPRedirection response: HTTPURLResponse,
    newRequest request: URLRequest,
    completionHandler: @escaping (URLRequest?) -> Void
  ) {
    completionHandler(nil)
  }
}

/// One origin, one cookie jar, one refresh at a time. No web view or bearer-token proxy.
actor APIClient {
  static let sessionExpired = Notification.Name("SpectralNature.sessionExpired")
  let baseURL: URL
  private let transport: URLSession
  private let keychain: KeychainTokenStore
  private var cookies: [HTTPCookie] = []
  private var csrf = ""
  private var tail: Task<Void, Never>?

  init(baseURL: URL = AppEnvironment.serverURL) {
    self.baseURL = baseURL
    keychain = KeychainTokenStore(
      service: "com.torrescapital.spectralnature", account: "session:\(baseURL.absoluteString)")
    let config = URLSessionConfiguration.ephemeral
    config.httpCookieStorage = nil
    config.httpShouldSetCookies = false
    config.urlCache = nil
    config.timeoutIntervalForRequest = 65
    config.timeoutIntervalForResource = 90
    transport = URLSession(configuration: config, delegate: RedirectPolicy(), delegateQueue: nil)
    if let stored = keychain.readToken(), let data = stored.data(using: .utf8),
      let saved = try? JSONDecoder().decode([StoredCookie].self, from: data)
    {
      cookies = saved.compactMap { $0.cookie }.filter {
        $0.expiresDate.map { $0 > Date() } ?? false
      }
    }
  }

  func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
    try decode(await data(path, query: query))
  }

  func getIfChanged<T: Decodable>(
    _ path: String, query: [String: String] = [:], etag: String
  ) async throws -> (value: T?, etag: String) {
    let (payload, response) = try await request(
      path, query: query, headers: etag.isEmpty ? [:] : ["If-None-Match": etag])
    return (response.statusCode == 304 ? nil : try decode(payload),
            response.value(forHTTPHeaderField: "ETag") ?? "")
  }

  func post<T: Decodable>(_ path: String, body: Record = [:], key: String? = nil) async throws -> T
  {
    let encoded = try JSONEncoder().encode(body)
    return try decode(await data(path, method: "POST", body: encoded, key: key))
  }

  func patch<T: Decodable>(_ path: String, body: Record) async throws -> T {
    let encoded = try JSONEncoder().encode(body)
    return try decode(await data(path, method: "PATCH", body: encoded))
  }

  func dataset<T: Decodable>(_ name: String, params: Record = [:]) async throws -> T {
    let response: DatasetResponse<T> = try await post(
      "/api/datasets/\(name)", body: ["params": .object(params)])
    return response.payload
  }

  func upload(_ url: URL) async throws -> JSONValue {
    let permitted = url.startAccessingSecurityScopedResource()
    defer { if permitted { url.stopAccessingSecurityScopedResource() } }
    let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
    guard size > 0, size <= 5_242_880 else { throw APIError.fileTooLarge }
    let boundary = "sn-\(UUID().uuidString)"
    let filename = url.lastPathComponent.replacingOccurrences(of: "\"", with: "")
      .replacingOccurrences(of: "\r", with: "").replacingOccurrences(of: "\n", with: "")
    var body = Data(
      "--\(boundary)\r\nContent-Disposition: form-data; name=\"source\"; filename=\"\(filename)\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        .utf8)
    body.append(try Data(contentsOf: url))
    body.append(Data("\r\n--\(boundary)--\r\n".utf8))
    return try decode(
      await data(
        "/api/zopedia/sources/upload", method: "POST", body: body,
        contentType: "multipart/form-data; boundary=\(boundary)"))
  }

  func data(
    _ path: String, query: [String: String] = [:], method: String = "GET",
    body: Data? = nil, key: String? = nil, contentType: String = "application/json"
  ) async throws -> Data {
    try await request(path, query: query, method: method, body: body, key: key, contentType: contentType).0
  }

  private func request(
    _ path: String, query: [String: String] = [:], method: String = "GET",
    body: Data? = nil, key: String? = nil, contentType: String = "application/json",
    headers: [String: String] = [:]
  ) async throws -> (Data, HTTPURLResponse) {
    let previous = tail
    let operation = Task {
      await previous?.value
      try Task.checkCancellation()
      return try await self.perform(
        path, query: query, method: method, body: body, key: key, contentType: contentType, headers: headers)
    }
    tail = Task { _ = await operation.result }
    return try await withTaskCancellationHandler {
      try await operation.value
    } onCancel: {
      operation.cancel()
    }
  }

  func clearSession() async {
    await tail?.value
    cookies = []
    csrf = ""
    keychain.clearToken()
  }

  private func decode<T: Decodable>(_ data: Data) throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(T.self, from: data)
  }

  private func perform(
    _ path: String, query: [String: String], method: String,
    body: Data?, key: String?, contentType: String, headers: [String: String] = [:]
  ) async throws -> (Data, HTTPURLResponse) {
    precondition(path.hasPrefix("/api/"))
    if method != "GET", csrf.isEmpty {
      let (payload, response) = try await send(
        "/api/auth/csrf", query: [:], method: "GET", body: nil, key: nil,
        contentType: "application/json")
      try validate(payload, response)
      let value = try JSONDecoder().decode(JSONValue.self, from: payload)
      csrf = value["csrfToken"].string
      guard !csrf.isEmpty else { throw APIError.invalidResponse }
    }
    var (payload, response) = try await send(
      path, query: query, method: method, body: body, key: key, contentType: contentType, headers: headers)
    try Task.checkCancellation()
    // Retry only an explicit CSRF rejection, before the backend mutation ran.
    if response.statusCode == 403, method != "GET",
      (try? JSONDecoder().decode(JSONValue.self, from: payload))?["error"].string
        == "Request verification failed."
    {
      csrf = ""
      let token: JSONValue = try decode(
        await perform(
          "/api/auth/csrf", query: [:], method: "GET", body: nil, key: nil,
          contentType: "application/json").0)
      csrf = token["csrfToken"].string
      (payload, response) = try await send(
        path, query: query, method: method, body: body, key: key, contentType: contentType, headers: headers)
    }
    try Task.checkCancellation()
    if response.statusCode == 401 {
      cookies = []
      csrf = ""
      keychain.clearToken()
      if path != "/api/auth/login" {
        NotificationCenter.default.post(name: Self.sessionExpired, object: nil)
      }
    }
    if !(response.statusCode == 304 && method == "GET" && headers["If-None-Match"] != nil) {
      try validate(payload, response)
    }
    return (payload, response)
  }

  private func send(
    _ path: String, query: [String: String], method: String,
    body: Data?, key: String?, contentType: String, headers: [String: String] = [:]
  ) async throws -> (Data, HTTPURLResponse) {
    guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
      throw APIError.invalidResponse
    }
    components.path = path
    components.queryItems =
      query.isEmpty
      ? nil : query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
    guard let url = components.url else { throw APIError.invalidResponse }
    var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)
    request.httpMethod = method
    request.httpBody = body
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    request.setValue("SpectralNature-iOS/1.0", forHTTPHeaderField: "User-Agent")
    for (header, value) in headers { request.setValue(value, forHTTPHeaderField: header) }
    cookies.removeAll { $0.expiresDate.map { $0 <= Date() } ?? false }
    let matching = cookies.filter {
      $0.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == url.host
        && url.path.hasPrefix($0.path) && (!$0.isSecure || url.scheme == "https")
    }
    for (header, value) in HTTPCookie.requestHeaderFields(with: matching) {
      request.setValue(value, forHTTPHeaderField: header)
    }
    if method != "GET" {
      request.setValue(
        baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
        forHTTPHeaderField: "Origin")
      request.setValue(csrf, forHTTPHeaderField: "X-CSRF-Token")
      request.setValue(contentType, forHTTPHeaderField: "Content-Type")
      request.setValue(String(body?.count ?? 0), forHTTPHeaderField: "Content-Length")
    }
    if let key { request.setValue(key, forHTTPHeaderField: "Idempotency-Key") }
    let (payload, response) = try await transport.data(for: request)
    guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
    let headers = http.allHeaderFields.reduce(into: [String: String]()) {
      $0[String(describing: $1.key)] = String(describing: $1.value)
    }
    for cookie in HTTPCookie.cookies(withResponseHeaderFields: headers, for: url) {
      guard cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == baseURL.host
      else { continue }
      cookies.removeAll { $0.name == cookie.name && $0.path == cookie.path }
      if cookie.expiresDate.map({ $0 > Date() }) ?? true { cookies.append(cookie) }
    }
    let saved = cookies.map(StoredCookie.init)
    if saved.isEmpty {
      keychain.clearToken()
      return (payload, http)
    }
    if let encoded = try? JSONEncoder().encode(saved),
      let value = String(data: encoded, encoding: .utf8)
    {
      guard keychain.saveToken(value) else {
        throw APIError.server(0, "The session could not be saved securely.")
      }
    }
    return (payload, http)
  }

  private func validate(_ payload: Data, _ response: HTTPURLResponse) throws {
    guard (200..<300).contains(response.statusCode) else {
      let value = try? JSONDecoder().decode(JSONValue.self, from: payload)
      let error = value?["error"].string ?? ""
      let message = error.isEmpty ? value?["detail"].string ?? "" : error
      throw APIError.server(
        response.statusCode, message.isEmpty ? "Request failed (\(response.statusCode))." : message)
    }
  }
}

private struct StoredCookie: Codable {
  let name: String
  let value: String
  let domain: String
  let path: String
  let secure: Bool
  let expires: Date?
  init(_ cookie: HTTPCookie) {
    name = cookie.name
    value = cookie.value
    domain = cookie.domain
    path = cookie.path
    secure = cookie.isSecure
    expires = cookie.expiresDate
  }
  var cookie: HTTPCookie? {
    var properties: [HTTPCookiePropertyKey: Any] = [
      .name: name, .value: value, .domain: domain, .path: path, .secure: secure ? "TRUE" : "FALSE",
    ]
    if let expires { properties[.expires] = expires }
    return HTTPCookie(properties: properties)
  }
}
