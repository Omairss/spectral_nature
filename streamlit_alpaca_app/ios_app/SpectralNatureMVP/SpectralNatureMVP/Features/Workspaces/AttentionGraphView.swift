import SwiftUI

struct AttentionGraphView: View {
  private struct Node: Identifiable {
    let id: Int
    let x: Double
    let y: Double
    let label: String
    let group: String
  }
  private struct Edge {
    let x1: Double
    let y1: Double
    let x2: Double
    let y2: Double
  }
  let artifact: JSONValue
  @State private var selected: Int?
  @State private var zoom = 1.0
  private var traces: [JSONValue] { artifact["spec"]["figure"]["data"].array }
  private var nodes: [Node] {
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
        let label =
          text.isEmpty
          ? detail.components(separatedBy: "\n").first ?? ""
          : text.components(separatedBy: "<br>").first ?? ""
        output.append(Node(id: output.count, x: x, y: y, label: label, group: trace["name"].string))
      }
    }
    return output
  }
  private var edges: [Edge] {
    traces.filter { $0["mode"].string == "lines" }.compactMap { trace in
      let x = trace["x"].array
      let y = trace["y"].array
      guard x.count == 2, y.count == 2, let x1 = x[0].number, let x2 = x[1].number,
        let y1 = y[0].number, let y2 = y[1].number
      else { return nil }
      return Edge(x1: x1, y1: y1, x2: x2, y2: y2)
    }
  }
  var body: some View {
    let nodes = nodes
    let edges = edges
    let minX = nodes.map(\.x).min() ?? 0
    let minY = nodes.map(\.y).min() ?? 0
    let spanX = max((nodes.map(\.x).max() ?? 1) - minX, 0.000001)
    let spanY = max((nodes.map(\.y).max() ?? 1) - minY, 0.000001)
    let selectedNode = nodes.first { $0.id == selected }
    VStack(alignment: .leading, spacing: 16) {
      HStack {
        Image(systemName: "minus.magnifyingglass")
        Slider(value: $zoom, in: 1...3).accessibilityLabel("Network zoom")
        Image(systemName: "plus.magnifyingglass")
        Button("Reset") {
          zoom = 1
          selected = nil
        }.font(.caption)
      }.padding(.horizontal, 20)
      if let node = selectedNode {
        VStack(alignment: .leading, spacing: 8) {
          Text(node.group).font(.caption).foregroundStyle(Theme.violet)
          Text(node.label).font(.subheadline).textSelection(.enabled)
        }.padding(.horizontal, 20)
      }
      GeometryReader { geometry in
        let side = geometry.size.width * zoom
        ScrollView([.horizontal, .vertical]) {
          ZStack(alignment: .topLeading) {
            Canvas { context, _ in
              for edge in edges {
                let connected =
                  selectedNode.map {
                    ($0.x == edge.x1 && $0.y == edge.y1) || ($0.x == edge.x2 && $0.y == edge.y2)
                  } ?? false
                var path = Path()
                path.move(to: position(edge.x1, edge.y1, minX, minY, spanX, spanY, side))
                path.addLine(to: position(edge.x2, edge.y2, minX, minY, spanX, spanY, side))
                context.stroke(
                  path,
                  with: .color(
                    connected ? Theme.violet : .white.opacity(selectedNode == nil ? 0.18 : 0.07)),
                  lineWidth: connected ? 2 : 1)
              }
            }
            ForEach(nodes) { node in
              Button {
                selected = node.id
              } label: {
                VStack(spacing: 3) {
                  Circle().fill(selected == node.id ? Theme.violet : Theme.teal).frame(
                    width: selected == node.id ? 14 : 9, height: selected == node.id ? 14 : 9)
                  if !node.label.isEmpty {
                    Text(node.label).font(.system(size: 9)).foregroundStyle(.primary).lineLimit(1)
                  }
                }.frame(width: 68, height: 44).contentShape(Rectangle())
              }.buttonStyle(.plain).position(
                position(node.x, node.y, minX, minY, spanX, spanY, side)
              ).accessibilityLabel(node.label.isEmpty ? node.group : node.label)
            }
          }.frame(width: side, height: side)
        }
      }
    }.padding(.top, 16).navigationTitle("Attention network").navigationBarTitleDisplayMode(.inline)
      .workbench().researchContext(
        "Daily Brief attention network; artifact_id=\(artifact["artifact_id"].string); selected=\(selectedNode?.label ?? "")"
      )
  }
  private func position(
    _ x: Double, _ y: Double, _ minX: Double, _ minY: Double, _ spanX: Double, _ spanY: Double,
    _ side: CGFloat
  ) -> CGPoint {
    CGPoint(
      x: 36 + (x - minX) / spanX * (side - 72), y: side - 36 - (y - minY) / spanY * (side - 72))
  }
}
