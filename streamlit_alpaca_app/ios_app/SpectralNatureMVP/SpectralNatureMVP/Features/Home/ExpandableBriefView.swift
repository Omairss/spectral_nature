import SwiftUI

/// Authored by the brief producer; a replacement must fit the same surrounding sentence.
struct ExpandableBrief: Codable {
  struct Paragraph: Codable, Identifiable {
    struct Segment: Codable, Identifiable {
      let id: String
      let text: String
      let expansion: String?
    }
    let id: String
    let segments: [Segment]
  }
  let paragraphs: [Paragraph]
}

struct ExpandableBriefView: View {
  let brief: ExpandableBrief

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      ForEach(brief.paragraphs) { paragraph in
        ExpandableBriefParagraph(paragraph: paragraph)
      }
    }
  }
}

private struct ExpandableBriefParagraph: View {
  let paragraph: ExpandableBrief.Paragraph
  @Environment(\.accessibilityReduceMotion) private var reduceMotion
  @State private var expanded: Set<String> = []

  private var content: AttributedString {
    paragraph.segments.reduce(into: AttributedString()) { output, segment in
      let isExpanded = expanded.contains(segment.id)
      var run = AttributedString(isExpanded ? segment.expansion ?? segment.text : segment.text)
      if let expansion = segment.expansion, !expansion.isEmpty {
        var destination = URLComponents()
        destination.scheme = "snbrief"
        destination.host = "expand"
        destination.queryItems = [URLQueryItem(name: "segment", value: segment.id)]
        run.link = destination.url
        run.foregroundColor = isExpanded ? Theme.text : Theme.teal
        run.underlineStyle = Text.LineStyle(
          pattern: .dot, color: Theme.teal.opacity(isExpanded ? 0.35 : 0.65))
      }
      output.append(run)
    }
  }

  var body: some View {
    Text(content)
      .font(.body)
      .foregroundStyle(Theme.text)
      .lineSpacing(5)
      .textSelection(.enabled)
      .fixedSize(horizontal: false, vertical: true)
      .frame(maxWidth: .infinity, alignment: .leading)
      .tint(Theme.teal)
      .contentTransition(.opacity)
      .environment(\.openURL, OpenURLAction { url in
        guard url.scheme == "snbrief", url.host == "expand",
          let id = URLComponents(url: url, resolvingAgainstBaseURL: false)?
            .queryItems?.first(where: { $0.name == "segment" })?.value,
          paragraph.segments.contains(where: {
            $0.id == id && !($0.expansion ?? "").isEmpty
          })
        else { return .discarded }
        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.22)) {
          if expanded.contains(id) { expanded.remove(id) }
          else { expanded.insert(id) }
        }
        return .handled
      })
  }
}
