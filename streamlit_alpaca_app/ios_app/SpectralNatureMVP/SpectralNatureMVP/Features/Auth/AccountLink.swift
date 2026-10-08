import Foundation

struct AccountLink: Equatable {
  let action: String
  let token: String
  init(url: URL) throws {
    let incoming: URL
    if url.scheme == "spectralnature", url.host == "account",
      let raw = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: {
        $0.name == "link"
      })?.value,
      let nested = URL(string: raw)
    {
      incoming = nested
    } else {
      incoming = url
    }
    let origin = AppEnvironment.serverURL
    guard incoming.scheme == origin.scheme, incoming.host == origin.host,
      incoming.port == origin.port, incoming.user == nil, incoming.password == nil,
      ["/", "/login", ""].contains(incoming.path),
      let query = URLComponents(url: incoming, resolvingAgainstBaseURL: false)?.queryItems
    else { throw APIError.server(400, "Use an account link for this app environment.") }
    let tokens = query.filter { ["invite_token", "reset_token"].contains($0.name) }
    guard tokens.count == 1, let item = tokens.first, let token = item.value,
      token.range(of: "^[A-Za-z0-9_-]{32,256}$", options: .regularExpression) != nil
    else { throw APIError.server(400, "Account link is invalid.") }
    self.action = item.name == "invite_token" ? "invite" : "reset"
    self.token = token
  }
}
