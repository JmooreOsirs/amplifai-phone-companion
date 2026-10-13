import Foundation
import Darwin

@main
struct ContactProvenanceChecks {
    private enum Failure: Error { case check(String) }
    private static func require(_ value: @autoclosure () -> Bool, _ reason: String) throws {
        guard value() else { throw Failure.check(reason) }
    }

    static func main() {
        do {
            try sourceFactsPreserveOwnerDecisions()
            try completeInventoryCounts()
            print("macOS contact provenance and 672-contact workspace checks passed")
        } catch {
            FileHandle.standardError.write(Data("Contact provenance check failed: \(error)\n".utf8))
            Darwin.exit(1)
        }
    }

    private static func sourceFactsPreserveOwnerDecisions() throws {
        let json = #"[{"id":7,"name":"Ada Lovelace","phoneCount":1,"phoneEnds":["0123"],"nameFields":"first","organizationField":false},{"id":19,"name":"Prince","phoneCount":1,"phoneEnds":["0124"],"nameFields":"first","organizationField":true},{"id":31,"name":"王","phoneCount":1,"phoneEnds":["0125"],"nameFields":"last"},{"id":73,"name":"","phoneCount":1,"phoneEnds":["0126"],"nameFields":"missing"},{"id":99,"name":"Legacy Display","phoneCount":1,"phoneEnds":["0127"]},{"id":105,"name":"Future Source","phoneCount":1,"phoneEnds":["0128"],"nameFields":"future_schema"}]"#
        let contacts = try JSONDecoder().decode([ContactPreview].self, from: Data(json.utf8))
        try require(contacts[0].nameFields == .first && !contacts[0].nameFieldDescription.contains("last"),
                    "Combined display text must not fabricate separate last-name evidence")
        try require(contacts[4].nameFields == nil && contacts[4].organizationField == nil &&
                    contacts[4].nameFieldDescription.contains("unavailable"),
                    "Legacy previews must decode without claiming name-field provenance")
        try require(contacts[5].nameFields == .unknown && contacts[5].nameFieldDescription.contains("unavailable"),
                    "An unknown future source label must not fabricate provenance or drop a contact")
        guard var workspace = ContactWorkspace(previews: contacts, expectedCount: 6) else {
            throw Failure.check("All six stable IDs must survive preview decoding")
        }
        try require(workspace.mainIDs == [7, 19, 31, 99, 105] && workspace.reviewIDs == [73],
                    "Mononyms, international names and optional organization hints must not add a new classifier")
        try require(workspace.move([19], from: .main, to: .review) &&
                    workspace.move([19], from: .review, to: .main) && workspace.knownIDs.count == 6,
                    "Source facts must not prevent reversible owner group decisions or remove stable IDs")
    }

    private static func completeInventoryCounts() throws {
        let rows = (1...672).map { id in
            #"{"id":\#(id),"name":"\#(id <= 482 ? "Person" : "Example LLC") \#(id)","phoneCount":1,"phoneEnds":["0123"]}"#
        }.joined(separator: ",")
        let contacts = try JSONDecoder().decode([ContactPreview].self, from: Data("[\(rows)]".utf8))
        guard var workspace = ContactWorkspace(previews: contacts, expectedCount: 672) else {
            throw Failure.check("The complete 672-contact snapshot must be required")
        }
        try require(workspace.knownIDs.count == 672 && workspace.visible.count == 200 &&
                    workspace.mainCount == 482 && workspace.reviewCount == 190,
                    "Total, page, Main and Review counts must remain distinct")
        workspace.show(.all)
        workspace.search("672")
        try require(workspace.filteredIDs == [672] && workspace.knownIDs.count == 672 &&
                    workspace.mainIDs.count == 482 && workspace.reviewIDs.count == 190,
                    "Search must not narrow the whole-snapshot or per-group action scopes")
        try require(ContactWorkspace(previews: Array(contacts.prefix(200)), expectedCount: 672) == nil,
                    "A displayed page must never stand in for the full eligible inventory")
    }
}
