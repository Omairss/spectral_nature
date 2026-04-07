import Foundation

struct QueryRequestBody: Encodable {
    let operation: String
    let name: String
    let params: [String: JSONValue]
}

struct QueryResponseBody: Decodable {
    let request: QueryRequestEcho?
    let resultType: String
    let payload: JSONValue
    let provenance: JSONValue?
    let messages: [String]

    enum CodingKeys: String, CodingKey {
        case request
        case resultType = "result_type"
        case payload
        case provenance
        case messages
    }
}

struct QueryRequestEcho: Decodable {
    let operation: String
    let name: String
    let params: [String: JSONValue]
}

struct AuthStatusResponse: Decodable {
    let available: Bool?
    let ready: Bool?
    let hasUsers: Bool?
    let mode: String?
    let databaseAuthEnabled: Bool

    enum CodingKeys: String, CodingKey {
        case available
        case ready
        case hasUsers = "has_users"
        case mode
        case databaseAuthEnabled = "database_auth_enabled"
    }
}

struct LoginRequestBody: Encodable {
    let email: String
    let password: String
}

struct LoginResponseBody: Decodable {
    let ok: Bool
    let tokenType: String
    let accessToken: String
    let refreshToken: String
    let scopes: [String]
    let context: [String: JSONValue]?
    let accessTokenExpiresAt: String?
    let refreshTokenExpiresAt: String?

    enum CodingKeys: String, CodingKey {
        case ok
        case tokenType = "token_type"
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case scopes
        case context
        case accessTokenExpiresAt = "access_token_expires_at"
        case refreshTokenExpiresAt = "refresh_token_expires_at"
    }
}

struct MeResponse: Decodable {
    let authenticated: Bool
    let context: [String: JSONValue]?
}

struct BasicResponse: Decodable {
    let ok: Bool
}
