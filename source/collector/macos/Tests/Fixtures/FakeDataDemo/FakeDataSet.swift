import Foundation

/// Deterministic fictional contacts and interaction counts for the FAKE DATA demo and its checks.
/// Every name is invented; phone endings are generated digits. Nothing here comes from a phone.
enum FakeDataSet {
    struct Contact {
        let id: Int
        let name: String
        let phoneEnds: [String]
        let calls: Int
        let messages: Int
    }

    struct SampleReport: Equatable {
        struct Row: Equatable {
            let name: String
            let calls: Int
            let messages: Int
        }
        let shared: Int
        let total: Int
        let calls: Int
        let messages: Int
        let withoutActivity: Int
        let notIncluded: Int
        let mostActive: [Row]
    }

    static let email = "demo.owner@example.invalid"
    static let course = "FAKE DATA DEMO course"
    static let observedEarliest = "2025-11-03T09:15:00Z"
    static let observedLatest = "2026-10-12T18:40:00Z"

    private static let firstNames = ["Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Gray", "Harper",
                                     "Indigo", "Jules", "Kai", "Lane", "Morgan", "Nova", "Oakley", "Parker",
                                     "Quinn", "Reese", "Sage", "Tatum", "Umber", "Vale", "Wren", "Yael"]
    private static let lastNames = ["Applecrest", "Brightwater", "Cindervale", "Dunmorrow", "Embergate", "Foxglen",
                                    "Gildenrow", "Hollowmere", "Ivorydale", "Juniperfell", "Kestrelby", "Larkwood", "Mistral"]

    /// 312 contacts: every 26th has no phone number and every 37th has an LLC name, so both land in Review.
    static let contacts: [Contact] = (1...312).map { id in
        let last = lastNames[(id - 1) / firstNames.count]
        let name = id % 37 == 0 ? "\(last) Repairs LLC" : "\(firstNames[(id - 1) % firstNames.count]) \(last)"
        let hasPhone = id % 26 != 0
        return Contact(id: id, name: name,
                       phoneEnds: hasPhone ? [String(format: "%04d", (id * 37) % 10_000)] : [],
                       calls: hasPhone ? (id * 7) % 11 : 0,
                       messages: hasPhone ? (id * 13) % 31 : 0)
    }

    static var availableCalls: Int { contacts.reduce(0) { $0 + $1.calls } }
    static var availableMessages: Int { contacts.reduce(0) { $0 + $1.messages } }

    static func preview(_ contact: Contact) -> [String: Any] {
        ["id": contact.id, "name": contact.name, "phoneCount": contact.phoneEnds.count, "phoneEnds": contact.phoneEnds]
    }

    /// Selection-driven sample of the downstream report. It is computed on this Mac from fictional data;
    /// the real report is produced by the AMPLIFai website only after an account Save.
    static func sampleReport(selected: Set<Int>) -> SampleReport? {
        let chosen = contacts.filter { selected.contains($0.id) }
        guard !chosen.isEmpty else { return nil }
        let ranked = chosen.filter { $0.calls + $0.messages > 0 }
            .sorted { ($0.calls + $0.messages, -$0.id) > ($1.calls + $1.messages, -$1.id) }
        return SampleReport(shared: chosen.count, total: contacts.count,
                            calls: chosen.reduce(0) { $0 + $1.calls },
                            messages: chosen.reduce(0) { $0 + $1.messages },
                            withoutActivity: chosen.count - ranked.count,
                            notIncluded: contacts.count - chosen.count,
                            mostActive: ranked.prefix(10).map { SampleReport.Row(name: $0.name, calls: $0.calls, messages: $0.messages) })
    }
}
