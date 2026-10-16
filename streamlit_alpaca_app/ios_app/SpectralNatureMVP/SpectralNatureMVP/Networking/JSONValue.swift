import Foundation

enum JSONValue: Codable, Equatable, Sendable {
  case string(String)
  case number(Double)
  case bool(Bool)
  case object([String: JSONValue])
  case array([JSONValue])
  case null

  init(from decoder: Decoder) throws {
    let container = try decoder.singleValueContainer()
    if container.decodeNil() {
      self = .null
      return
    }
    if let value = try? container.decode(Bool.self) {
      self = .bool(value)
      return
    }
    if let value = try? container.decode(Double.self) {
      self = .number(value)
      return
    }
    if let value = try? container.decode(String.self) {
      self = .string(value)
      return
    }
    if let value = try? container.decode([String: JSONValue].self) {
      self = .object(value)
      return
    }
    if let value = try? container.decode([JSONValue].self) {
      self = .array(value)
      return
    }
    throw DecodingError.dataCorruptedError(
      in: container,
      debugDescription: "Unsupported JSON value"
    )
  }

  func encode(to encoder: Encoder) throws {
    var container = encoder.singleValueContainer()
    switch self {
    case let .string(value):
      try container.encode(value)
    case let .number(value):
      try container.encode(value)
    case let .bool(value):
      try container.encode(value)
    case let .object(value):
      try container.encode(value)
    case let .array(value):
      try container.encode(value)
    case .null:
      try container.encodeNil()
    }
  }

  var dictionaryValue: [String: JSONValue]? {
    if case let .object(value) = self {
      return value
    }
    return nil
  }

  var array: [JSONValue] {
    if case let .array(value) = self { return value }
    return []
  }
  var records: [Record] { array.compactMap(\.dictionaryValue) }
  var string: String {
    switch self {
    case let .string(value): return value
    case let .number(value): return value.formatted(.number.precision(.fractionLength(0...3)))
    case let .bool(value): return value ? "Yes" : "No"
    default: return ""
    }
  }
  var number: Double? {
    if case let .number(value) = self, value.isFinite { return value }
    return nil
  }
  subscript(_ key: String) -> JSONValue { dictionaryValue?[key] ?? .null }

}
