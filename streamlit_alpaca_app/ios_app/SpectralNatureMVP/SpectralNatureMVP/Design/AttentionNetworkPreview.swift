import SwiftUI

struct AttentionNetworkPreview: View {
  let artifact: JSONValue

  var body: some View {
    let network = AttentionNetwork(artifact: artifact)
    let nodes = network.nodes
    let edges = network.edges
    let lowX = nodes.map(\.x).min() ?? 0
    let highX = nodes.map(\.x).max() ?? 1
    let lowY = nodes.map(\.y).min() ?? 0
    let highY = nodes.map(\.y).max() ?? 1

    VStack(alignment: .leading, spacing: 8) {
      HStack {
        Text("Attention").font(.subheadline.weight(.medium))
        Spacer()
        Image(systemName: "arrow.up.left.and.arrow.down.right").font(.caption)
          .foregroundStyle(Theme.secondaryText)
      }
      Canvas { context, size in
        let scale = min((size.width - 28) / max(highX - lowX, 0.00001),
                        (size.height - 28) / max(highY - lowY, 0.00001))
        func point(_ x: Double, _ y: Double) -> CGPoint {
          CGPoint(x: size.width / 2 + (x - (lowX + highX) / 2) * scale,
                  y: size.height / 2 - (y - (lowY + highY) / 2) * scale)
        }
        for edge in edges {
          var path = Path()
          path.move(to: point(edge.x1, edge.y1))
          path.addLine(to: point(edge.x2, edge.y2))
          context.stroke(path, with: .color(Theme.secondaryText.opacity(0.24)), lineWidth: 0.7)
        }
        for node in nodes {
          let center = point(node.x, node.y)
          let radius = 4.0
          var shape = Path()
          switch node.movement {
          case .up, .down:
            let sign = node.movement == .up ? -1.0 : 1.0
            shape.move(to: CGPoint(x: center.x, y: center.y + sign * radius))
            shape.addLine(to: CGPoint(x: center.x - radius, y: center.y - sign * radius))
            shape.addLine(to: CGPoint(x: center.x + radius, y: center.y - sign * radius))
            shape.closeSubpath()
          case .context:
            shape.move(to: CGPoint(x: center.x, y: center.y - radius * 0.65))
            shape.addLine(to: CGPoint(x: center.x + radius * 0.65, y: center.y))
            shape.addLine(to: CGPoint(x: center.x, y: center.y + radius * 0.65))
            shape.addLine(to: CGPoint(x: center.x - radius * 0.65, y: center.y))
            shape.closeSubpath()
          case .flat, .unavailable:
            shape.addEllipse(in: CGRect(x: center.x - 3, y: center.y - 3, width: 6, height: 6))
          }
          if node.movement == .context {
            context.stroke(shape, with: .color(Theme.secondaryText.opacity(0.5)), lineWidth: 0.7)
          } else {
            context.fill(shape, with: .color(node.movement.color))
          }
          if !node.label.isEmpty {
            context.draw(Text(node.label).font(.system(size: 8)).foregroundColor(Theme.secondaryText),
                         at: CGPoint(x: center.x, y: center.y + 10))
          }
        }
      }
      .frame(height: 190)
      .accessibilityHidden(true)
    }
    .padding(.vertical, 12)
    .foregroundStyle(Theme.text)
    .accessibilityElement(children: .ignore)
    .accessibilityLabel("Attention network, \(nodes.count) nodes. Open full network")
  }
}
