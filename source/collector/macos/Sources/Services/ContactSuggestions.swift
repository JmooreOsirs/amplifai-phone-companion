import Foundation

enum ContactSuggestionKind: String, CaseIterable, Hashable {
    case organization
    case emoji
    case formatting

    var title: String {
        switch self {
        case .organization: return "Organization-like names"
        case .emoji: return "Names with emoji"
        case .formatting: return "Names to check for formatting"
        }
    }

    var reason: String {
        switch self {
        case .organization:
            return "The name contains a whole-word marker such as LLC, Inc, Corp, Ltd, Company, Foundation or Association. This is a name clue, not a business classification."
        case .emoji:
            return "The name contains an emoji presentation character or emoji variation selector. Emoji alone says nothing about the person."
        case .formatting:
            return "The name has outer whitespace, repeated spaces, a line break, a control character or a replacement character. Check the original name before deciding."
        }
    }

    static func matches(_ name: String) -> Set<Self> {
        var result: Set<Self> = []
        let markers: Set<String> = ["llc", "pllc", "inc", "corp", "ltd", "company", "foundation", "association"]
        let words = name.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber })
        if words.contains(where: { markers.contains(String($0)) }) { result.insert(.organization) }
        if name.unicodeScalars.contains(where: { $0.properties.isEmojiPresentation || $0.value == 0xFE0F }) {
            result.insert(.emoji)
        }
        if name != name.trimmingCharacters(in: .whitespacesAndNewlines) ||
            name.contains("  ") || name.contains("\n") || name.contains("\r") ||
            name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) || $0.value == 0xFFFD }) {
            result.insert(.formatting)
        }
        return result
    }
}
