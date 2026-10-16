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
