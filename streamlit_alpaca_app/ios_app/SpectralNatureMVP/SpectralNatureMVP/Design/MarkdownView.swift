import SwiftUI

/// Block structure stays native; inline links/emphasis use Foundation Markdown.
struct MarkdownView: View {
  let text: String
  var internalLinks: [String: String] = [:]
  var onPage: ((String) -> Void)? = nil
  private var content: String {
    guard
      let expression = try? NSRegularExpression(pattern: "\\[\\[([^\\]|]+)(?:\\|([^\\]]+))?\\]\\]")
    else { return text }
    var output = text
    for match in expression.matches(in: text, range: NSRange(text.startIndex..., in: text))
      .reversed()
    {
      guard let range = Range(match.range, in: text),
        let targetRange = Range(match.range(at: 1), in: text)
      else { continue }
      let target = String(text[targetRange]).trimmingCharacters(in: .whitespaces)
      let label = Range(match.range(at: 2), in: text).map { String(text[$0]) } ?? target
      var destination = URLComponents()
      destination.scheme = "snknowledge"
      destination.host = "page"
      if let id = internalLinks[target.lowercased()] {
        destination.queryItems = [URLQueryItem(name: "id", value: id)]
        output.replaceSubrange(range, with: "[\(label)](\(destination.url!.absoluteString))")
      } else {
        output.replaceSubrange(range, with: label)
      }
    }
    return output
  }
  private struct Block: Identifiable {
    enum Kind {
      case paragraph
      case heading(Int)
      case code, table
    }
    let id: Int
    let kind: Kind
    let lines: [String]
  }
  private var blocks: [Block] {
    var output: [Block] = []
    var pending: [String] = []
    var fenced = false
    func flush() {
      if !pending.isEmpty {
        output.append(Block(id: output.count, kind: fenced ? .code : .paragraph, lines: pending))
        pending = []
      }
    }
    let lines = content.components(separatedBy: .newlines)
    var index = 0
    while index < lines.count {
      let line = lines[index]
      if line.hasPrefix("```") {
        flush()
        fenced.toggle()
        index += 1
        continue
      }
      if fenced {
        pending.append(line)
        index += 1
        continue
      }
      if line.contains("|"), index + 1 < lines.count,
        lines[index + 1].contains("---"), lines[index + 1].contains("|")
      {
        flush()
        var table = [line]
        index += 2
        while index < lines.count && lines[index].contains("|") && !lines[index].isEmpty {
          table.append(lines[index])
          index += 1
        }
        output.append(Block(id: output.count, kind: .table, lines: table))
        continue
      }
      if line.isEmpty {
        flush()
      } else if line.hasPrefix("#") {
        flush()
        let level = line.prefix(while: { $0 == "#" }).count
        output.append(
          Block(
            id: output.count, kind: .heading(level),
            lines: [String(line.dropFirst(level)).trimmingCharacters(in: .whitespaces)]))
      } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
        flush()
        output.append(Block(id: output.count, kind: .paragraph, lines: ["• " + line.dropFirst(2)]))
      } else {
        pending.append(line)
      }
      index += 1
    }
    flush()
    return output
  }
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      ForEach(blocks) { block in
        switch block.kind {
        case .paragraph:
          inline(block.lines.joined(separator: "\n")).font(.subheadline).lineSpacing(5)
        case let .heading(level):
          inline(block.lines.joined()).font(level < 3 ? .headline : .subheadline.weight(.semibold))
            .padding(.top, 6)
        case .code:
          ScrollView(.horizontal) {
            Text(block.lines.joined(separator: "\n")).font(.system(.caption, design: .monospaced))
              .padding(12)
          }.background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))
        case .table:
          ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
              ForEach(Array(block.lines.enumerated()), id: \.offset) { index, line in
                HStack(alignment: .top, spacing: 14) {
                  ForEach(Array(cells(line).enumerated()), id: \.offset) { _, cell in
                    inline(cell).font(index == 0 ? .caption.weight(.semibold) : .caption).frame(
                      width: 150, alignment: .leading)
                  }
                }.padding(.vertical, 10)
                Divider()
              }
            }
          }
        }
      }
    }.textSelection(.enabled).tint(Theme.teal)
      .environment(
        \.openURL,
        OpenURLAction { url in
          if url.scheme == "snknowledge",
            let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(
              where: { $0.name == "id" })?.value, internalLinks.values.contains(id), let onPage
          {
            onPage(id)
            return .handled
          }
          return ["http", "https"].contains(url.scheme?.lowercased() ?? "")
            ? .systemAction : .discarded
        })
  }
  private func inline(_ text: String) -> Text {
    Text(
      (try? AttributedString(
        markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(text))
  }
  private func cells(_ line: String) -> [String] {
    var cells = line.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    if cells.first == "" { cells.removeFirst() }
    if cells.last == "" { cells.removeLast() }
    return cells
  }
}
