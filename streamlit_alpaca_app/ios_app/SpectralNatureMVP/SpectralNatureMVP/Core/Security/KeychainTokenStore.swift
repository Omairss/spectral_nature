import Foundation
import Security

final class KeychainTokenStore {
  private let service: String
  private let account: String

  init(service: String, account: String) {
    self.service = service
    self.account = account
  }

  func readToken() -> String? {
    var query = baseQuery()
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne

    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess, let data = item as? Data else {
      return nil
    }
    return String(data: data, encoding: .utf8)
  }

  @discardableResult
  func saveToken(_ token: String?) -> Bool {
    guard let token, !token.isEmpty, let data = token.data(using: .utf8) else {
      return clearToken()
    }
    let updated = SecItemUpdate(
      baseQuery() as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if updated == errSecSuccess { return true }
    guard updated == errSecItemNotFound else { return false }
    var query = baseQuery()
    query[kSecValueData as String] = data
    query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    let status = SecItemAdd(query as CFDictionary, nil)
    return status == errSecSuccess
  }

  @discardableResult
  func clearToken() -> Bool {
    let status = SecItemDelete(baseQuery() as CFDictionary)
    return status == errSecSuccess || status == errSecItemNotFound
  }

  private func baseQuery() -> [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }
}
