import SwiftUI

/// Presentation only. The model owns verified full-dataset selection and review locks.
struct ContactReviewView: View {
    @ObservedObject var model: CollectorModel
    let workspace: ContactWorkspace
    let wellHeight: CGFloat
    @Binding var search: String
    @Binding var showingGroupTools: Bool
    @Binding var confirmingBulkMove: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.section) {
            controls
            contactWell
            organizingTools
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Brand.Space.item) { includeAll; Spacer(minLength: 0); workspaceFilters(workspace) }
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    includeAll
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 8) { workspaceFilters(workspace) }
                        HStack(spacing: 8) { workspaceFilters(workspace, compact: true) }
                    }
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Brand.Space.item) {
                    searchField.frame(minWidth: 180, maxWidth: .infinity)
                    scopeSelectionRow(workspace)
                }
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    searchField
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: Brand.Space.item) { scopeSelectionRow(workspace) }
                        VStack(alignment: .leading, spacing: Brand.Space.item) { scopeSelectionRow(workspace) }
                    }
                }
            }
            Text("Select all and Deselect all apply across every page in this group and search. Uncheck exceptions below.")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Brand.Space.item)
        .background(Brand.background, in: RoundedRectangle(cornerRadius: Brand.Radius.control))
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control).stroke(Brand.border, lineWidth: 1))
    }

    private var includeAll: some View {
        Toggle(isOn: Binding(
            get: { !workspace.knownIDs.isEmpty && workspace.knownIDs.isSubset(of: model.selectedIDs) },
            set: { on in if on { model.selectAllVerifiedContacts() } else { model.clearAllVerifiedContacts() } }
        )) {
            Text("Include all \(workspace.knownIDs.count.formatted()) contacts")
                .font(Brand.bodyFont(15).weight(.bold))
                .foregroundStyle(Brand.heading)
        }
        .toggleStyle(.checkbox)
        .frame(minHeight: 32)
        .disabled(!model.canEditWorkspace)
        .accessibilityLabel("Include all \(workspace.knownIDs.count) contacts, \(model.selectedIDs.count) selected")
    }

    private var searchField: some View {
        TextField("Search \(workspace.group.rawValue) by name", text: $search)
            .textFieldStyle(.plain)
            .font(Brand.bodyFont(15))
            .padding(.horizontal, 16)
            .frame(minHeight: 40)
            .background(Brand.panel, in: Capsule())
            .overlay(Capsule().stroke(Brand.border, lineWidth: 1))
            .accessibilityLabel("Search contacts by name in \(workspace.group.rawValue)")
            .onChange(of: search) { _, value in
                if value.unicodeScalars.count > 120 { search = String(value.unicodeScalars.prefix(120)) }
                else { model.searchWorkspace(value) }
            }
    }

    private var contactWell: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            if workspace.filteredCount == 0 {
                Text("No matches here. Your selections are kept. Clear the search or choose another group.")
                    .foregroundStyle(Brand.muted)
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: Brand.Layout.contactMinimum), spacing: Brand.Space.item)],
                              alignment: .leading, spacing: Brand.Space.item) {
                        ForEach(workspace.visible) { contact in
                            contactRow(contact, workspace: workspace)
                        }
                    }
                    .padding(Brand.Space.item)
                }
                .frame(height: wellHeight)
                .background(Brand.background.shadow(.inner(color: Brand.background.opacity(0.85), radius: 5, y: 2)),
                            in: RoundedRectangle(cornerRadius: Brand.Radius.control))
                .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control).stroke(Brand.border, lineWidth: 1))
                .id("\(workspace.group.rawValue)-\(workspace.query)-\(workspace.pageStart)")
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: Brand.Space.item) { pagination }
                    VStack(alignment: .leading, spacing: Brand.Space.item) { pagination }
                }
            }
        }
    }

    @ViewBuilder private var pagination: some View {
        if workspace.hasPreviousPage {
            Button("Previous page") { model.previousWorkspacePage() }
                .buttonStyle(SecondaryButton())
                .accessibilityLabel("Previous page of contacts")
        }
        if workspace.hasNextPage {
            Button("Next page") { model.nextWorkspacePage() }
                .buttonStyle(SecondaryButton())
                .accessibilityLabel("Next page of contacts")
        }
        Text("\(workspace.pageStart + 1)–\(workspace.pageStart + workspace.visible.count) of \(workspace.filteredCount) matches")
            .font(Brand.bodyFont(16))
            .foregroundStyle(Brand.muted)
    }

    private var organizingTools: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            Button(showingGroupTools
                   ? "Hide organizing · \(model.markedGroupMoveIDs.count) marked"
                   : "Organize groups · \(model.markedGroupMoveIDs.count) marked") {
                showingGroupTools.toggle()
            }
            .buttonStyle(SecondaryButton())
            if showingGroupTools {
                Text("Marks are for moving groups, not selecting contacts to save. Moving to Review clears their selection; Undo restores it.")
                    .font(Brand.bodyFont(16))
                    .foregroundStyle(Brand.muted)
                if workspace.group == .all {
                    Text("Choose Main or Review to mark a group move, or move one contact below.")
                        .font(Brand.bodyFont(16))
                        .foregroundStyle(Brand.muted)
                } else if workspace.filteredCount > 0 {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) { groupMoveActions(workspace) }
                        VStack(alignment: .leading, spacing: 10) { groupMoveActions(workspace) }
                    }
                }
            }
            if model.canUndoGroupMove {
                Button("Undo last group move") { model.undoContactMove() }
                    .buttonStyle(SecondaryButton())
            }
            DisclosureGroup("About Main and Review") {
                Text("Main starts with a display name and supported phone number. Review holds missing details or possible organization clues for your decision. One name is enough. These are local suggestions; nothing changes on your phone.")
                    .font(Brand.bodyFont(16))
                    .foregroundStyle(Brand.muted)
                    .padding(.top, 8)
            }
        }
    }

    @ViewBuilder private func workspaceFilters(_ workspace: ContactWorkspace, compact: Bool = false) -> some View {
        ForEach(ContactGroup.allCases, id: \.rawValue) { group in
            let count = group == .all ? workspace.knownIDs.count : group == .main ? workspace.mainCount : workspace.reviewCount
            let label = compact ? "\(group.rawValue)\n\(count.formatted())" : "\(group.rawValue) · \(count.formatted())"
            if workspace.group == group {
                Button(label) { model.showContactGroup(group) }
                    .buttonStyle(LimeButton())
                    .accessibilityAddTraits(.isSelected)
            } else {
                Button(label) { model.showContactGroup(group) }
                    .buttonStyle(SecondaryButton())
            }
        }
    }

    @ViewBuilder private func scopeSelectionRow(_ workspace: ContactWorkspace) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(workspace.scopeLabel)
                .font(Brand.bodyFont(13).weight(.semibold))
                .foregroundStyle(Brand.heading)
                .fixedSize(horizontal: false, vertical: true)
            Text("\(model.selectedScopedCount) of \(workspace.filteredCount) selected")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
        }
        .accessibilityElement(children: .combine)
        Button("Select all · \(workspace.filteredCount.formatted())") { model.selectScopedContacts() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canSelectScopedContacts)
            .accessibilityLabel("Select all \(workspace.filteredCount) contacts in \(workspace.scopeLabel), across all pages")
        Button("Deselect all") { model.clearScopedContacts() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canClearScopedContacts)
            .accessibilityLabel("Deselect all \(workspace.filteredCount) contacts in this scope")
    }

    @ViewBuilder private func groupMoveActions(_ workspace: ContactWorkspace) -> some View {
        let destination: ContactGroup = workspace.group == .main ? .review : .main
        Button("Move all \(workspace.filteredCount) matches to \(destination.rawValue)") {
            if model.beginBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace)
        Button("Mark all \(workspace.filteredCount) matches") { model.markAllMatchingForGroupMove() }
            .buttonStyle(SecondaryButton())
            .disabled(!model.canEditWorkspace)
        Button("Move \(model.markedGroupMoveIDs.count) marked to \(destination.rawValue)") {
            if model.beginMarkedBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace || model.markedGroupMoveIDs.isEmpty)
        if !model.markedGroupMoveIDs.isEmpty {
            Button("Clear group marks") { model.clearGroupMoveMarks() }
                .buttonStyle(SecondaryButton())
        }
    }

    private func contactRow(_ contact: ContactPreview, workspace: ContactWorkspace) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(isOn: Binding(
                get: { model.selectedIDs.contains(contact.id) },
                set: { _ in model.toggle(contact.id) }
            )) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(contact.name.isEmpty ? "Unnamed contact" : contact.name)
                        .font(Brand.bodyFont(15).weight(.bold))
                        .lineLimit(2)
                        .help(contact.name.isEmpty ? "Unnamed contact" : contact.name)
                        .foregroundStyle(Brand.heading)
                    Text(contact.phoneEnds.isEmpty
                         ? "No supported phone number"
                         : contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                        .font(Brand.bodyFont(13))
                        .lineLimit(2)
                        .foregroundStyle(Brand.muted)
                }
            }
            .toggleStyle(.checkbox)
            .accessibilityLabel("Include \(contact.name.isEmpty ? "unnamed contact" : contact.name) in selected contacts")
            .disabled(!model.canEditWorkspace)
            Text(workspace.isInReview(contact.id) ? "Review" : "Main")
                .font(Brand.bodyFont(12).weight(.semibold))
                .foregroundStyle(workspace.isInReview(contact.id) ? Brand.body : Brand.muted)
            if let reason = workspace.reason(for: contact.id) {
                Text(reason)
                    .font(Brand.bodyFont(13))
                    .fixedSize(horizontal: false, vertical: true)
                    .foregroundStyle(Brand.body)
            }
            DisclosureGroup("Source details") {
                VStack(alignment: .leading, spacing: 6) {
                    Text(contact.nameFieldDescription)
                    Text("\(contact.phoneCount) supported phone numbers")
                    if !contact.phoneEnds.isEmpty {
                        Text(contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if contact.organizationField == true {
                        Text("Organization field supplied. This is a hint, not verified business identity.")
                    }
                }
                .font(Brand.bodyFont(16))
                .foregroundStyle(Brand.muted)
                .padding(.top, 6)
            }
            .font(Brand.bodyFont(13))
            .frame(minHeight: 24, alignment: .leading)
            if showingGroupTools {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) { contactGroupActions(contact, workspace: workspace) }
                    VStack(alignment: .leading, spacing: 10) { contactGroupActions(contact, workspace: workspace) }
                }
            }
        }
        .padding(Brand.Space.item)
        .frame(maxWidth: .infinity, minHeight: 120, alignment: .topLeading)
        .background(Brand.raisedSurface, in: RoundedRectangle(cornerRadius: Brand.Radius.card))
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.card)
            .stroke(model.selectedIDs.contains(contact.id) ? Brand.lime : Brand.border,
                    lineWidth: model.selectedIDs.contains(contact.id) ? 1.5 : 1))
        .shadow(color: Brand.background.opacity(0.85), radius: 7, y: 6)
    }

    @ViewBuilder private func contactGroupActions(_ contact: ContactPreview, workspace: ContactWorkspace) -> some View {
        if showingGroupTools && workspace.group != .all {
            Button(model.markedGroupMoveIDs.contains(contact.id) ? "Unmark group move" : "Mark for group move") {
                model.markContactForGroupMove(contact.id)
            }
            .buttonStyle(SecondaryButton())
            .accessibilityLabel("\(model.markedGroupMoveIDs.contains(contact.id) ? "Unmark" : "Mark") \(contact.name.isEmpty ? "unnamed contact" : contact.name) for group move")
            .disabled(!model.canEditWorkspace)
        }
        Button(workspace.isInReview(contact.id) ? "Move to Main" : "Move to Review") {
            model.moveContact(contact.id, to: workspace.isInReview(contact.id) ? .main : .review)
        }
        .buttonStyle(SecondaryButton())
        .disabled(!model.canEditWorkspace)
    }

}
