import Charts
import SceneKit
import SwiftUI

struct MatrixChart: View {
  let columns: [String]
  let rows: [String]
  let values: [[Double?]]
  var fraction = false
  @State private var selected: String?
  private let extent: Double
  init(columns: [String], rows: [String], values: [[Double?]], fraction: Bool = false) {
    self.columns = columns
    self.rows = rows
    self.values = values
    self.fraction = fraction
    extent = max(values.flatMap { $0 }.compactMap { $0 }.map(abs).max() ?? 1, 0.000001)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let selected { Text(selected).font(.caption).monospacedDigit() }
      ScrollView(.horizontal) {
        ScrollView(.vertical) {
          LazyVStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 3) {
              Color.clear.frame(width: 100, height: 28)
              ForEach(Array(columns.enumerated()), id: \.offset) { _, title in
                Text(title).font(.caption2).lineLimit(2).frame(width: 64, height: 28)
              }
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { i, title in
              HStack(spacing: 3) {
                Text(title).font(.caption2).lineLimit(2).frame(width: 100, alignment: .leading)
                ForEach(Array(columns.enumerated()), id: \.offset) { j, column in
                  let value = i < values.count && j < values[i].count ? values[i][j] : nil
                  let label = Format.percent(value, fraction: fraction)
                  Button {
                    selected = "\(title) · \(column): \(label)"
                  } label: {
                    Text(label).font(.system(size: 10, design: .monospaced)).frame(
                      width: 64, height: 36
                    )
                    .background(
                      value.map { Theme.tone($0).opacity(0.1 + 0.65 * abs($0) / extent) }
                        ?? Color.white.opacity(0.03), in: RoundedRectangle(cornerRadius: 3))
                  }.buttonStyle(.plain).accessibilityLabel("\(title), \(column): \(label)")
                }
              }
            }
          }.frame(width: CGFloat(103 + columns.count * 67), alignment: .leading)
        }.frame(height: min(430, CGFloat(rows.count * 39 + 34)))
      }
      HStack {
        Text(Format.percent(-extent, fraction: fraction))
        Spacer()
        Text("0")
        Spacer()
        Text(Format.percent(extent, fraction: fraction))
      }
      .font(.caption2).foregroundStyle(.secondary)
    }
  }
}

struct TreemapChart: View {
  struct Tile: Identifiable {
    let id: Int
    let label: String
    let group: String
    let weight: Double
    let color: Double?
    var context = ""
  }
  let tiles: [Tile]
  let weightLabel: String
  let colorLabel: String
  var money = false
  @State private var selection: Int?
  private var valid: [Tile] {
    tiles.filter { $0.weight.isFinite && $0.weight > 0 }.sorted { $0.weight > $1.weight }
  }
  private var extent: Double { max(valid.compactMap(\.color).map(abs).max() ?? 1, 0.000001) }
  private struct Cell {
    let tile: Tile
    let rect: CGRect
  }
  // Balanced area bisection preserves exact weights and keeps tiny names inspectable below.
  private func layout(_ values: [Tile], in rect: CGRect) -> [Cell] {
    guard !values.isEmpty else { return [] }
    if values.count == 1 { return [Cell(tile: values[0], rect: rect)] }
    let total = values.reduce(0) { $0 + $1.weight }
    var sum = 0.0
    var split = 1
    for i in 0..<(values.count - 1) {
      sum += values[i].weight
      split = i + 1
      if sum >= total / 2 { break }
    }
    let ratio = CGFloat(sum / total)
    let first: CGRect
    let second: CGRect
    if rect.width >= rect.height {
      first = CGRect(x: rect.minX, y: rect.minY, width: rect.width * ratio, height: rect.height)
      second = CGRect(
        x: first.maxX, y: rect.minY, width: rect.width - first.width, height: rect.height)
    } else {
      first = CGRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height * ratio)
      second = CGRect(
        x: rect.minX, y: first.maxY, width: rect.width, height: rect.height - first.height)
    }
    return layout(Array(values.prefix(split)), in: first)
      + layout(Array(values.dropFirst(split)), in: second)
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      GeometryReader { geometry in
        ZStack(alignment: .topLeading) {
          ForEach(layout(valid, in: CGRect(origin: .zero, size: geometry.size)), id: \.tile.id) {
            cell in
            Button {
              selection = cell.tile.id
            } label: {
              VStack(spacing: 3) {
                if cell.rect.width > 35 && cell.rect.height > 25 {
                  Text(cell.tile.label).font(.caption.weight(.semibold)).lineLimit(1)
                    .minimumScaleFactor(0.7)
                }
                if cell.rect.width > 55 && cell.rect.height > 48 {
                  Text(Format.percent(cell.tile.color)).font(.system(size: 9)).lineLimit(1)
                }
              }.frame(width: max(0, cell.rect.width - 2), height: max(0, cell.rect.height - 2))
                .background(
                  Theme.tone(cell.tile.color).opacity(
                    0.15 + 0.65 * abs(cell.tile.color ?? 0) / extent)
                )
                .overlay(
                  Rectangle().stroke(selection == cell.tile.id ? Color.white : .clear, lineWidth: 1)
                )
            }.buttonStyle(.plain).position(x: cell.rect.midX, y: cell.rect.midY)
              .accessibilityLabel(
                "\(cell.tile.label), \(weightLabel) \(Format.number(cell.tile.weight)), \(colorLabel) \(Format.percent(cell.tile.color))"
              )
          }
        }
      }.frame(height: 270)
      Picker("Inspect", selection: $selection) {
        Text("Select position").tag(Int?.none)
        ForEach(valid) {
          Text(
            !$0.context.isEmpty
              ? $0.context : $0.group.isEmpty ? $0.label : "\($0.group) · \($0.label)"
          ).tag(Optional($0.id))
        }
      }
      if let tile = valid.first(where: { $0.id == selection }) {
        if !tile.context.isEmpty { Text(tile.context).font(.caption).textSelection(.enabled) }
        Metrics(items: [
          (weightLabel, money ? Format.money(tile.weight) : Format.number(tile.weight)),
          (colorLabel, Format.percent(tile.color)),
        ])
      }
      Text("Area: \(weightLabel) · Color: \(colorLabel)").font(.caption2).foregroundStyle(
        .secondary)
    }
  }
}

struct ScatterPlot: View {
  let rows: [Record]
  let x: String
  let y: String
  let label: String
  let xTitle: String
  let yTitle: String
  var color: String? = nil
  var size: String? = nil
  var scale = 1.0
  @State private var selectedX: Double?
  private var sizeMaximum: Double {
    max(rows.compactMap { row in size.flatMap { row.number($0) }.map(abs) }.max() ?? 1, 0.000001)
  }
  private var selected: Record? {
    guard let selectedX else { return nil }
    return rows.filter { $0.number(x) != nil && $0.number(y) != nil }.min {
      abs(($0.number(x) ?? 0) * scale - selectedX) < abs(($1.number(x) ?? 0) * scale - selectedX)
    }
  }
  var body: some View {
    let sizeLimit = sizeMaximum
    VStack(alignment: .leading, spacing: 8) {
      if let row = selected {
        Text(
          "\(row.text(label)) · \(xTitle) \(Format.number(row.number(x).map { $0 * scale })) · \(yTitle) \(Format.number(row.number(y).map { $0 * scale }))"
        )
        .font(.caption).monospacedDigit()
        if let color {
          Text(
            "\(color.replacingOccurrences(of: "_", with: " ").capitalized): \(Format.number(row.number(color)))"
          ).font(.caption2)
        }
        if let size {
          Text("\(size.capitalized): \(Format.number(row.number(size)))").font(.caption2)
        }
      }
      Chart(Array(rows.enumerated()), id: \.offset) { _, row in
        if let a = row.number(x), let b = row.number(y) {
          PointMark(x: .value(xTitle, a * scale), y: .value(yTitle, b * scale))
            .foregroundStyle(Theme.tone(row.number(color ?? y)))
            .symbolSize(
              size.flatMap { row.number($0) }.map { 20 + 160 * abs($0) / sizeLimit } ?? 32)
        }
      }.chartXSelection(value: $selectedX).chartXAxisLabel(xTitle).chartYAxisLabel(yTitle)
        .chartXScale(domain: .automatic(includesZero: false)).chartYScale(
          domain: .automatic(includesZero: false)
        )
        .frame(height: 240)
      Text(
        [
          color.map { "Color: " + $0.replacingOccurrences(of: "_", with: " ").capitalized },
          size.map { "Size: " + $0.capitalized },
        ].compactMap { $0 }.joined(separator: " · ")
      ).font(.caption2).foregroundStyle(.secondary)
    }
  }
}

struct SpatialPlot: View {
  struct Point: Equatable {
    let label: String
    let x: Double
    let y: Double
    let z: Double
    let color: Double?
  }
  let points: [Point]
  let axes: [String]
  var surface = false
  var colorLabel = ""
  @State private var selection: Int?
  @State private var reset = UUID()
  @State private var inspecting = false
  @State private var search = ""
  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      if points.isEmpty {
        Text("Values unavailable").font(.caption).foregroundStyle(.secondary)
      } else {
        SpatialScene(
          points: points, axes: axes, surface: surface, selection: $selection, reset: reset
        )
        .frame(height: 300).clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityLabel(
          "Three dimensional plot. Rotate with one finger; pinch to zoom. Values available in Inspect."
        )
        ForEach(axes.indices, id: \.self) { index in
          let values = points.map { index == 0 ? $0.x : index == 1 ? $0.y : $0.z }
          Text("\(axes[index]) · \(Format.number(values.min())) to \(Format.number(values.max()))")
            .font(.caption2).foregroundStyle(.secondary)
        }
        HStack {
          Text(axes.joined(separator: " · ")).font(.caption2).foregroundStyle(.secondary)
          Spacer()
          Button("Reset view") { reset = UUID() }.font(.caption)
        }
        if !colorLabel.isEmpty {
          Text(
            "Color: \(colorLabel) · \(Format.number(points.compactMap(\.color).min())) to \(Format.number(points.compactMap(\.color).max()))"
          ).font(.caption2).foregroundStyle(.secondary)
        }
        Button(
          selection.flatMap { points.indices.contains($0) ? points[$0].label : nil }
            ?? "Inspect values"
        ) { inspecting = true }.font(.subheadline)
        if let selection, points.indices.contains(selection) {
          let point = points[selection]
          Metrics(
            items: [
              (axes[0], Format.number(point.x)), (axes[1], Format.number(point.y)),
              (axes[2], Format.number(point.z)),
            ] + (colorLabel.isEmpty ? [] : [(colorLabel, Format.number(point.color))]))
        }
      }
    }.sheet(isPresented: $inspecting) {
      NavigationStack {
        List {
          ForEach(
            Array(points.enumerated()).filter {
              search.isEmpty || $0.element.label.localizedCaseInsensitiveContains(search)
            }, id: \.offset
          ) { index, point in
            Button {
              selection = index
              inspecting = false
            } label: {
              VStack(alignment: .leading, spacing: 6) {
                Text(point.label).font(.subheadline)
                Text(
                  "\(axes[0]) \(Format.number(point.x)) · \(axes[1]) \(Format.number(point.y)) · \(axes[2]) \(Format.number(point.z))"
                ).font(.caption2).foregroundStyle(.secondary)
              }
            }.buttonStyle(.plain)
          }
        }.searchable(text: $search, prompt: "Symbol or contract").navigationTitle("Inspect values")
          .navigationBarTitleDisplayMode(.inline)
          .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Close") { inspecting = false } }
          }
          .scrollContentBackground(.hidden).workbench()
      }.preferredColorScheme(.dark)
    }
  }
}

private struct SpatialScene: UIViewRepresentable {
  let points: [SpatialPlot.Point]
  let axes: [String]
  let surface: Bool
  @Binding var selection: Int?
  let reset: UUID
  class Coordinator: NSObject {
    var parent: SpatialScene
    var lastPoints: [SpatialPlot.Point] = []
    var lastReset: UUID?
    init(_ parent: SpatialScene) { self.parent = parent }
    @objc func tap(_ gesture: UITapGestureRecognizer) {
      guard let view = gesture.view as? SCNView else { return }
      let hit = view.hitTest(
        gesture.location(in: view), options: [.searchMode: SCNHitTestSearchMode.all.rawValue])
      if let name = hit.compactMap({ $0.node.name }).first(where: { $0.hasPrefix("point:") }),
        let index = Int(name.dropFirst(6))
      {
        parent.selection = index
      }
    }
  }
  func makeCoordinator() -> Coordinator { Coordinator(self) }
  func makeUIView(context: Context) -> SCNView {
    let view = SCNView()
    view.backgroundColor = UIColor(Theme.panel)
    view.allowsCameraControl = true
    view.defaultCameraController.interactionMode = .orbitTurntable
    view.antialiasingMode = .multisampling4X
    view.addGestureRecognizer(
      UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap)))
    return view
  }
  func updateUIView(_ view: SCNView, context: Context) {
    context.coordinator.parent = self
    guard context.coordinator.lastPoints != points || context.coordinator.lastReset != reset else {
      highlight(view)
      return
    }
    context.coordinator.lastPoints = points
    context.coordinator.lastReset = reset
    let scene = SCNScene()
    let camera = SCNNode()
    camera.camera = SCNCamera()
    camera.camera?.zFar = 100
    camera.camera?.fieldOfView = 38
    camera.position = SCNVector3(3.4, 2.8, 4.6)
    camera.look(at: SCNVector3Zero)
    scene.rootNode.addChildNode(camera)
    func bounds(_ values: [Double]) -> (Double, Double) {
      let low = values.min() ?? 0
      return (low, max((values.max() ?? 1) - low, 0.000001))
    }
    let bx = bounds(points.map(\.x))
    let by = bounds(points.map(\.y))
    let bz = bounds(points.map(\.z))
    func position(_ point: SpatialPlot.Point) -> SCNVector3 {
      SCNVector3(
        Float((point.x - bx.0) / bx.1 * 2 - 1), Float((point.z - bz.0) / bz.1 * 2 - 1),
        Float((point.y - by.0) / by.1 * 2 - 1))
    }
    let extent = max(points.compactMap(\.color).map(abs).max() ?? 1, 0.000001)
    for (index, point) in points.enumerated() {
      let sphere = SCNSphere(radius: 0.028)
      sphere.segmentCount = 8
      sphere.firstMaterial?.diffuse.contents = UIColor(
        Theme.tone(point.color).opacity(0.45 + 0.55 * abs(point.color ?? 0) / extent))
      sphere.firstMaterial?.lightingModel = .constant
      let node = SCNNode(geometry: sphere)
      node.position = position(point)
      node.name = "point:\(index)"
      scene.rootNode.addChildNode(node)
    }
    if surface {
      let xs = Array(Set(points.map(\.x))).sorted()
      let ys = Array(Set(points.map(\.y))).sorted()
      var lookup: [Double: [Double: SpatialPlot.Point]] = [:]
      for point in points where lookup[point.x]?[point.y] == nil {
        lookup[point.x, default: [:]][point.y] = point
      }
      var vertices: [SCNVector3] = []
      if xs.count > 1 && ys.count > 1 {
        for i in 0..<(xs.count - 1) {
          for j in 0..<(ys.count - 1) {
            if let a = lookup[xs[i]]?[ys[j]], let b = lookup[xs[i + 1]]?[ys[j]],
              let c = lookup[xs[i]]?[ys[j + 1]], let d = lookup[xs[i + 1]]?[ys[j + 1]]
            {
              vertices += [
                position(a), position(b), position(c), position(b), position(d), position(c),
              ]
            }
          }
        }
      }
      if !vertices.isEmpty {
        let geometry = SCNGeometry(
          sources: [SCNGeometrySource(vertices: vertices)],
          elements: [
            SCNGeometryElement(indices: Array(0..<Int32(vertices.count)), primitiveType: .triangles)
          ])
        let material = SCNMaterial()
        material.diffuse.contents = UIColor(Theme.violet.opacity(0.35))
        material.isDoubleSided = true
        material.lightingModel = .constant
        geometry.materials = [material]
        scene.rootNode.addChildNode(SCNNode(geometry: geometry))
      }
    }
    for index in axes.indices {
      let endpoints: [SCNVector3]
      switch index {
      case 0: endpoints = [SCNVector3(-1, -1, -1), SCNVector3(1, -1, -1)]
      case 1: endpoints = [SCNVector3(-1, -1, -1), SCNVector3(-1, -1, 1)]
      default: endpoints = [SCNVector3(-1, -1, -1), SCNVector3(-1, 1, -1)]
      }
      let axis = SCNGeometry(
        sources: [SCNGeometrySource(vertices: endpoints)],
        elements: [SCNGeometryElement(indices: [Int32(0), 1], primitiveType: .line)])
      axis.firstMaterial?.diffuse.contents = UIColor.lightGray
      axis.firstMaterial?.lightingModel = .constant
      scene.rootNode.addChildNode(SCNNode(geometry: axis))
      for coordinate in endpoints.suffix(1) {
        let title = axes[index]
        let text = SCNText(string: title, extrusionDepth: 0)
        text.font = .systemFont(ofSize: 10)
        text.firstMaterial?.diffuse.contents = UIColor.lightGray
        text.firstMaterial?.lightingModel = .constant
        let node = SCNNode(geometry: text)
        node.scale = SCNVector3(0.016, 0.016, 0.016)
        node.position = coordinate
        node.constraints = [SCNBillboardConstraint()]
        scene.rootNode.addChildNode(node)
      }
    }
    view.scene = scene
    view.pointOfView = camera
    view.defaultCameraController.target = SCNVector3Zero
    highlight(view)
  }
  private func highlight(_ view: SCNView) {
    for node in view.scene?.rootNode.childNodes ?? [] {
      guard let name = node.name, name.hasPrefix("point:") else { continue }
      let selected = Int(name.dropFirst(6)) == selection
      node.scale = selected ? SCNVector3(2.2, 2.2, 2.2) : SCNVector3(1, 1, 1)
      node.geometry?.firstMaterial?.emission.contents = selected ? UIColor.white : UIColor.black
    }
  }
}

extension Array where Element == Record {
  func spatial(x: String, y: String, z: String, label: String, color: String, zScale: Double = 1)
    -> [SpatialPlot.Point]
  {
    compactMap { row in
      guard let a = row.number(x), let b = row.number(y), let c = row.number(z) else { return nil }
      return SpatialPlot.Point(
        label: row.text(label), x: a, y: b, z: c * zScale, color: row.number(color))
    }
  }
}
