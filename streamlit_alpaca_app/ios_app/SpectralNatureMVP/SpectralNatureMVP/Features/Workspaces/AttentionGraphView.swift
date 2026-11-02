import SwiftUI

struct AttentionGraphView: View {
  private typealias Movement = AttentionNetwork.Movement
  let artifact: JSONValue
  @State private var selected: Int?
  @State private var zoom = 1.0
  private var nodes: [AttentionNetwork.Node] { AttentionNetwork(artifact: artifact).nodes }
  private var edges: [AttentionNetwork.Edge] { AttentionNetwork(artifact: artifact).edges }
  var body: some View {
    let nodes = nodes
    let edges = edges
    let minX = nodes.map(\.x).min() ?? 0
    let minY = nodes.map(\.y).min() ?? 0
    let spanX = max((nodes.map(\.x).max() ?? 1) - minX, 0.000001)
    let spanY = max((nodes.map(\.y).max() ?? 1) - minY, 0.000001)
    let selectedNode = nodes.first { $0.id == selected }
    VStack(alignment: .leading, spacing: 16) {
      HStack(spacing: 16) {
        ForEach([Movement.up, .down, .flat, .context], id: \.label) { movement in
          Label(movement.label, systemImage: movement.icon).foregroundStyle(movement.color)
        }
      }.font(.caption).padding(.horizontal, 20).accessibilityElement(children: .combine)
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
          Text(node.title).font(.subheadline).textSelection(.enabled)
          Label(node.movement.label, systemImage: node.movement.icon)
            .font(.subheadline.weight(.medium)).foregroundStyle(node.movement.color)
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
                  Image(systemName: node.movement.icon).font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(node.movement.color)
                    .padding(4)
                    .overlay(Circle().stroke(selected == node.id ? Theme.violet : .clear, lineWidth: 2))
                  if !node.label.isEmpty || selected == node.id {
                    Text(node.title).font(.system(size: 9)).foregroundStyle(Theme.text).lineLimit(1)
                  }
                }.frame(width: 68, height: 44).contentShape(Rectangle())
              }.buttonStyle(.plain).position(
                position(node.x, node.y, minX, minY, spanX, spanY, side)
              ).accessibilityLabel("\(node.title.isEmpty ? node.group : node.title), \(node.movement.label)")
            }
          }.frame(width: side, height: side)
        }
      }
    }.padding(.top, 16).navigationTitle("Attention network").navigationBarTitleDisplayMode(.inline)
      .workbench().researchContext(
        "Daily Brief attention network; artifact_id=\(artifact["artifact_id"].string); selected=\(selectedNode?.title ?? "")"
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
