import Foundation
struct ContactPreview: Decodable { let id: Int; let name: String; let phoneCount: Int; let phoneEnds: [String] }

import Foundation

enum ContactGroup: String, CaseIterable {
    case all = "All"
    case main = "Main"
    case review = "Review"
}

struct ContactWorkspace {
    private(set) var previews: [ContactPreview]
    private let allIDs: Set<Int>
    private(set) var reviewIDs: Set<Int>
    private let originalReasons: [Int: String]
    private var indices: [Int] = []
    private(set) var group: ContactGroup = .main
    private(set) var query = ""
    private(set) var pageStart = 0
    private let pageSize = 200

    init?(previews: [ContactPreview], expectedCount: Int) {
        let ids = Set(previews.map(\.id))
        guard previews.count == expectedCount,
              ids.count == expectedCount else { return nil }
        self.previews = previews
        allIDs = ids
        var reasons: [Int: String] = [:]
        for contact in previews {
            if contact.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                reasons[contact.id] = "Name is missing"
            } else if contact.phoneCount == 0 {
                reasons[contact.id] = "No supported phone number"
            } else if ContactSuggestionKind.matches(contact.name).contains(.organization) {
                reasons[contact.id] = "Organization-like name marker"
            }
        }
        originalReasons = reasons
        reviewIDs = Set(reasons.keys)
        rebuildFilter()
    }

    var mainCount: Int { previews.count - reviewIDs.count }
    var reviewCount: Int { reviewIDs.count }
    var mainIDs: Set<Int> { allIDs.subtracting(reviewIDs) }
    var knownIDs: Set<Int> { allIDs }
    var filteredCount: Int { indices.count }
    var filteredIDs: Set<Int> { Set(indices.map { previews[$0].id }) }
    /// True when the group or search narrows the scope below every contact.
    var isScoped: Bool { group != .all || !query.isEmpty }
    /// Names the current group and search; selection actions cover all its pages.
    var scopeLabel: String {
        let base = group == .all ? "All contacts" : "\(group.rawValue) contacts"
        let matching = query.isEmpty ? base : "\(base) matching “\(query)”"
        return "\(matching) · \(filteredCount) across all pages"
    }
    var visible: [ContactPreview] {
        indices[pageStart..<min(pageStart + pageSize, indices.count)].map { previews[$0] }
    }
    var hasNextPage: Bool { pageStart + pageSize < indices.count }
    var hasPreviousPage: Bool { pageStart > 0 }

    func isInReview(_ id: Int) -> Bool { reviewIDs.contains(id) }
    func contains(_ id: Int) -> Bool { allIDs.contains(id) }

    func reason(for id: Int) -> String? {
        guard reviewIDs.contains(id) else { return nil }
        return originalReasons[id] ?? "Moved to Review by you"
    }

    mutating func show(_ group: ContactGroup) {
        self.group = group
        rebuildFilter()
    }

    mutating func search(_ query: String) {
        self.query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        rebuildFilter()
    }

    mutating func nextPage() {
        guard hasNextPage else { return }
        pageStart += pageSize
    }

    mutating func previousPage() {
        guard hasPreviousPage else { return }
        pageStart = max(0, pageStart - pageSize)
    }

    mutating func move(_ ids: Set<Int>, from source: ContactGroup, to destination: ContactGroup) -> Bool {
        guard !ids.isEmpty, source != .all, destination != .all, source != destination,
              ids.isSubset(of: allIDs),
              source == .review ? ids.isSubset(of: reviewIDs) : ids.isDisjoint(with: reviewIDs) else {
            return false
        }
        if destination == .review { reviewIDs.formUnion(ids) }
        else { reviewIDs.subtract(ids) }
        rebuildFilter()
        return true
    }

    private mutating func rebuildFilter() {
        let needle = query.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
        indices = previews.indices.filter { index in
            let contact = previews[index]
            if group != .all && reviewIDs.contains(contact.id) != (group == .review) { return false }
            if needle.isEmpty { return true }
            return contact.name.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
                .contains(needle)
        }
        pageStart = 0
    }
}

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

let inputData = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let input = try JSONSerialization.jsonObject(with: inputData) as! [String:Any]
let previews = try JSONDecoder().decode([ContactPreview].self, from: JSONSerialization.data(withJSONObject: input["previews"]!))
var workspace = ContactWorkspace(previews: previews, expectedCount: previews.count)!
func snapshot(_ workspace: ContactWorkspace) -> [String:Any] {
 return ["group":workspace.group.rawValue,"query":workspace.query,"knownIDs":workspace.knownIDs.sorted(),"reviewIDs":workspace.reviewIDs.sorted(),"mainIDs":workspace.mainIDs.sorted(),"filteredIDs":workspace.filteredIDs.sorted(),"visibleIDs":workspace.visible.map(\.id),"mainCount":workspace.mainCount,"reviewCount":workspace.reviewCount,"filteredCount":workspace.filteredCount,"pageStart":workspace.pageStart,"isScoped":workspace.isScoped,"scopeLabel":workspace.scopeLabel,"hasNextPage":workspace.hasNextPage,"hasPreviousPage":workspace.hasPreviousPage,"reasons":Dictionary(uniqueKeysWithValues:workspace.knownIDs.sorted().map{(String($0),workspace.reason(for:$0) as Any? ?? NSNull())})]
}
let initial = snapshot(workspace)
var searches: [[String:Any]] = []
for group in ContactGroup.allCases {
 for query in input["queries"] as! [String] {
  workspace.show(group); workspace.search(query)
  searches.append(["group":group.rawValue,"query":query,"expected":snapshot(workspace)])
 }
}
workspace.show(.main); workspace.search("")
var moves: [[String:Any]] = []
for move in input["moves"] as! [[String:Any]] {
 let success=workspace.move(Set(move["ids"] as! [Int]), from:ContactGroup(rawValue:move["source"] as! String)!,to:ContactGroup(rawValue:move["destination"] as! String)!)
 moves.append(["operation":move,"success":success,"expected":snapshot(workspace)])
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["locale":Locale.current.identifier,"initial":initial,"searches":searches,"moves":moves],options:[.sortedKeys]))
