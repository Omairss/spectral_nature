import SwiftUI

struct SankeyChart: View {
  struct Node: Identifiable {
    let id: String
    let label: String
    let stage: Int
    let tone: Double
    var detail: String? = nil
    var labelLines: Int = 1
  }
  struct Link: Identifiable {
    let id: Int
    let source: String
    let target: String
    let magnitude: Double
    let signed: Double
    let label: String
  }
  let nodes: [Node]
  let links: [Link]
  let fraction: Bool
  @State private var selected: Int?
  @State private var expanded = false
  @ScaledMetric(relativeTo: .caption2) private var labelLineHeight: CGFloat = 14
  private func minimumHeight(_ node: Node) -> CGFloat {
    CGFloat(node.labelLines + (node.detail == nil ? 0 : 1)) * labelLineHeight + 8
  }
  private var reservedHeight: CGFloat {
    Dictionary(grouping: nodes, by: \.stage).values.map { stage in
      stage.reduce(CGFloat.zero) { $0 + minimumHeight($1) } + CGFloat(stage.count * 8)
    }.max() ?? 0
  }
  private var height: CGFloat {
    reservedHeight + 180
  }
  private struct Layout {
    let frames: [String: CGRect]
    let paths: [(Link, Path)]
  }
  private func layout(width: CGFloat) -> Layout {
    let weights = Dictionary(
      uniqueKeysWithValues: nodes.map { node in
        (
          node.id,
          max(
            links.filter { $0.source == node.id }.reduce(0) { $0 + $1.magnitude },
            links.filter { $0.target == node.id }.reduce(0) { $0 + $1.magnitude })
        )
      })
    let total = nodes.filter { $0.stage == 0 }.reduce(0) { $0 + (weights[$1.id] ?? 0) }
    let scale = total > 0 ? Double(max(1, height - reservedHeight - 20)) / total : 0
    var frames: [String: CGRect] = [:]
    for stage in 0...2 {
      var y: CGFloat = 10
      for node in nodes.filter({ $0.stage == stage }) {
        let flowHeight = CGFloat((weights[node.id] ?? 0) * scale)
        let slotHeight = max(flowHeight, minimumHeight(node))
        frames[node.id] = CGRect(
          x: CGFloat(stage) * width / 3 + 6, y: y + (slotHeight - flowHeight) / 2,
          width: 10, height: flowHeight)
        y += slotHeight + 8
      }
    }
    var sourceOffset: [String: CGFloat] = [:]
    var targetOffset: [String: CGFloat] = [:]
    var paths: [(Link, Path)] = []
    for link in links where link.magnitude > 0 {
      guard let source = frames[link.source], let target = frames[link.target] else { continue }
      let h = CGFloat(link.magnitude * scale)
      let sy = source.minY + sourceOffset[link.source, default: 0]
      let ty = target.minY + targetOffset[link.target, default: 0]
      sourceOffset[link.source, default: 0] += h
      targetOffset[link.target, default: 0] += h
      let sx = source.maxX
      let tx = target.minX
      let mid = (sx + tx) / 2
      var path = Path()
      path.move(to: CGPoint(x: sx, y: sy))
      path.addCurve(
        to: CGPoint(x: tx, y: ty), control1: CGPoint(x: mid, y: sy),
        control2: CGPoint(x: mid, y: ty))
      path.addLine(to: CGPoint(x: tx, y: ty + h))
      path.addCurve(
        to: CGPoint(x: sx, y: sy + h), control1: CGPoint(x: mid, y: ty + h),
        control2: CGPoint(x: mid, y: sy + h))
      path.closeSubpath()
      paths.append((link, path))
    }
    return Layout(frames: frames, paths: paths)
  }
  private func drawing(width: CGFloat) -> some View {
    let geometry = layout(width: width)
    return
      Canvas { context, _ in
        for (link, path) in geometry.paths {
          context.fill(
            path, with: .color(Theme.tone(link.signed).opacity(selected == link.id ? 0.8 : 0.35)))
        }
        for node in nodes {
          guard let frame = geometry.frames[node.id] else { continue }
          context.fill(
            Path(roundedRect: frame, cornerRadius: 3), with: .color(Theme.tone(node.tone)))
        }
      }
      .overlay {
        ForEach(nodes) { node in
          if let frame = geometry.frames[node.id] {
            VStack(alignment: .leading, spacing: 2) {
              Text(node.label).lineLimit(node.labelLines)
              if let detail = node.detail { Text(detail).monospacedDigit() }
            }.font(.caption2).frame(
              width: max(60, width / 3 - 26), alignment: .leading
            )
            .padding(2).shadow(color: .black, radius: 1)
            .position(x: frame.maxX + max(60, width / 3 - 26) / 2 + 3, y: frame.midY)
          }
        }
      }
      .frame(width: width, height: height)
      .contentShape(Rectangle())
      .gesture(
        SpatialTapGesture().onEnded { value in
          selected = geometry.paths.last(where: { $0.1.contains(value.location) })?.0.id
        })

  }
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      if let link = links.first(where: { $0.id == selected }) {
        Text(
          "\(link.label) · \(fraction ? Format.percent(link.signed, fraction: true) : Format.number(link.signed))"
        )
        .font(.caption).monospacedDigit().textSelection(.enabled)
      }
      GeometryReader { proxy in drawing(width: proxy.size.width) }.frame(height: height)
      Button("Expand chart") { expanded = true }.font(.caption)
      DisclosureGroup("Flow values") {
        ForEach(links.filter { link in nodes.contains { $0.id == link.target && $0.stage == 2 } }) {
          link in
          HStack(alignment: .top) {
            Text(link.label).font(.caption)
            Spacer()
            Text(
              fraction ? Format.percent(link.signed, fraction: true) : Format.number(link.signed)
            )
            .font(.caption.monospacedDigit()).foregroundStyle(Theme.tone(link.signed))
          }.padding(.vertical, 3)
        }
      }.font(.caption)
    }.accessibilityElement(children: .contain)
      .fullScreenCover(isPresented: $expanded) {
        NavigationStack {
          ScrollView([.horizontal, .vertical]) { drawing(width: 780).padding(20) }
            .workbench().navigationTitle("Contributors").navigationBarTitleDisplayMode(.inline)
            .toolbar {
              ToolbarItem(placement: .confirmationAction) { Button("Done") { expanded = false } }
            }
        }.preferredColorScheme(.dark)
      }
  }
}
