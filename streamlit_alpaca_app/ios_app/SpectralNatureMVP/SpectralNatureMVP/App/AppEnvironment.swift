import Foundation

enum AppEnvironment {
    static let apiBaseURLInfoKey = "SNApiBaseURL"
    static let fallbackBaseURL = "http://127.0.0.1:8080"

    static var defaultBaseURL: String {
        let value = Bundle.main.object(forInfoDictionaryKey: apiBaseURLInfoKey) as? String
        let trimmed = (value ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallbackBaseURL : trimmed
    }
}

