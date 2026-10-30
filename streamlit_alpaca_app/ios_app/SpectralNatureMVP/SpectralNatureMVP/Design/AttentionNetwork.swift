import SwiftUI

/// Shared geometry and source marker interpretation for the overview and full network.
struct AttentionNetwork {
  enum Movement {
    case up, down, flat, context, unavailable
    init(marker: String) {
      switch marker {
      case "triangle-up", "triangle-up-open": self = .up
      case "triangle-down", "triangle-down-open": self = .down
      case "circle": self = .flat
      case "circle-open", "diamond", "diamond-open": self = .context
      default: self = .unavailable
      }
    }
    var label: String {
      switch self {
      case .up: return "Up"
      case .down: return "Down"
      case .flat: return "Flat"
      case .context: return "Context"
      case .unavailable: return "Movement unavailable"
      }
    }
    var color: Color {
      switch self {
      case .up: return Theme.teal
      case .down: return Theme.coral
      case .flat: return Theme.blue
      case .context, .unavailable: return .gray
      }
    }
    var icon: String {
      switch self {
      case .up: return "arrowtriangle.up.fill"
      case .down: return "arrowtriangle.down.fill"
      case .flat: return "circle.fill"
      case .context: return "diamond"
      case .unavailable: return "questionmark.circle"
      }
    }
  }
  struct Node: Identifiable {
    let id: Int
    let x: Double
    let y: Double
    let label: String
    let title: String
    let group: String
    let movement: Movement
  }
  struct Edge {
    let x1: Double
    let y1: Double
    let x2: Double
    let y2: Double
  }
  let artifact: JSONValue
  private var traces: [JSONValue] { artifact["spec"]["figure"]["data"].array }
  var nodes: [Node] {
    var output: [Node] = []
    for trace in traces where trace["mode"].string.contains("markers") {
      for index in trace["x"].array.indices {
        guard index < trace["y"].array.count,
          let x = trace["x"].array[index].number, let y = trace["y"].array[index].number
        else { continue }
        let text = index < trace["text"].array.count ? trace["text"].array[index].string : ""
        let hover =
          index < trace["hovertext"].array.count ? trace["hovertext"].array[index].string : text
        let detail = hover.replacingOccurrences(of: "<br>", with: "\n").replacingOccurrences(
          of: "<[^>]+>", with: "", options: .regularExpression)
        let label = text.components(separatedBy: "<br>").first ?? ""
        let title = label.isEmpty ? detail.components(separatedBy: "\n").first ?? "" : label
        let markers = trace["marker"]["symbol"]
        let marker = index < markers.array.count ? markers.array[index].string : markers.string
        output.append(Node(id: output.count, x: x, y: y, label: label, title: title,
          group: trace["name"].string, movement: Movement(marker: marker)))
      }
    }
    return output
  }
  var edges: [Edge] {
    traces.filter { $0["mode"].string == "lines" }.compactMap { trace in
      let x = trace["x"].array
      let y = trace["y"].array
      guard x.count == 2, y.count == 2, let x1 = x[0].number, let x2 = x[1].number,
        let y1 = y[0].number, let y2 = y[1].number
      else { return nil }
      return Edge(x1: x1, y1: y1, x2: x2, y2: y2)
    }
  }
}
