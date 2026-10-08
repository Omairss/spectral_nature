import Foundation

enum AppEnvironment {
  static let developmentURL = URL(
    string: "https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io")!
  static var serverURL: URL {
    #if DEBUG
      if let raw = ProcessInfo.processInfo.environment["SN_SERVER_URL"],
        let url = URL(string: raw),
        url.scheme == "https"
          || (url.scheme == "http" && ["127.0.0.1", "localhost"].contains(url.host ?? ""))
      {
        return url
      }
    #endif
    return developmentURL
  }
}
