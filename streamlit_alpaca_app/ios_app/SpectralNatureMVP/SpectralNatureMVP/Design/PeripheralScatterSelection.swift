import Foundation

/// Select boundary layers, with partial layers spread around the whole boundary.
/// Coordinates are normalized for distance comparisons; hull depth is affine invariant.
enum PeripheralScatterSelection {
  struct Coordinate: Hashable {
    let x: Double
    let y: Double
  }

  static func indices(_ values: [Coordinate], limit: Int) -> [Int] {
    let valid = values.indices.filter { values[$0].x.isFinite && values[$0].y.isFinite }
    guard limit > 0 else { return [] }
    guard limit < valid.count else { return valid }
    let minX = valid.map { values[$0].x }.min() ?? 0
    let minY = valid.map { values[$0].y }.min() ?? 0
    let width = (valid.map { values[$0].x }.max() ?? minX) - minX
    let height = (valid.map { values[$0].y }.max() ?? minY) - minY
    let points = values.map {
      Coordinate(x: width > 0 ? ($0.x - minX) / width : 0,
                 y: height > 0 ? ($0.y - minY) / height : 0)
    }
    var groups: [Coordinate: [Int]] = [:]
    var remaining: [Int] = []
    for index in valid {
      if groups[values[index]] == nil { remaining.append(index) }
      groups[values[index], default: []].append(index)
    }
    var selected: [Int] = []
    var boundaryOrder: [Int] = []
    while !remaining.isEmpty && selected.count < limit {
      let boundary = hull(remaining, points: points)
      boundaryOrder += boundary
      let available = limit - selected.count
      selected += boundary.count <= available ? boundary : spread(boundary, count: available, points: points)
      let removed = Set(boundary)
      remaining.removeAll { removed.contains($0) }
    }
    // Coincident instruments share a position. Prefer distinct positions first,
    // then include remaining identities in their boundary order if K permits.
    if selected.count < limit {
      for index in boundaryOrder {
        for duplicate in (groups[values[index]] ?? []).dropFirst() where selected.count < limit {
          selected.append(duplicate)
        }
      }
    }
    return selected.sorted()
  }

  private static func hull(_ indices: [Int], points: [Coordinate]) -> [Int] {
    let sorted = indices.sorted {
      points[$0].x == points[$1].x ? points[$0].y < points[$1].y : points[$0].x < points[$1].x
    }
    guard sorted.count > 2 else { return sorted }
    func cross(_ a: Int, _ b: Int, _ c: Int) -> Double {
      (points[b].x - points[a].x) * (points[c].y - points[a].y)
        - (points[b].y - points[a].y) * (points[c].x - points[a].x)
    }
    func half(_ order: [Int]) -> [Int] {
      var result: [Int] = []
      for index in order {
        while result.count > 1 && cross(result[result.count - 2], result[result.count - 1], index) <= 0 {
          result.removeLast()
        }
        result.append(index)
      }
      return result
    }
    return Array(half(sorted).dropLast()) + Array(half(Array(sorted.reversed())).dropLast())
  }

  private static func spread(_ boundary: [Int], count: Int, points: [Coordinate]) -> [Int] {
    func distance(_ a: Coordinate, _ b: Coordinate) -> Double {
      let dx = a.x - b.x
      let dy = a.y - b.y
      return dx * dx + dy * dy
    }
    guard let first = boundary.max(by: {
      distance(points[$0], Coordinate(x: 0.5, y: 0.5)) < distance(points[$1], Coordinate(x: 0.5, y: 0.5))
    }) else { return [] }
    var selected = [first]
    while selected.count < count {
      let chosen = Set(selected)
      guard let next = boundary.filter({ !chosen.contains($0) }).max(by: { a, b in
        let da = selected.map { distance(points[a], points[$0]) }.min() ?? 0
        let db = selected.map { distance(points[b], points[$0]) }.min() ?? 0
        return da == db ? a > b : da < db
      }) else { break }
      selected.append(next)
    }
    return selected
  }
}
