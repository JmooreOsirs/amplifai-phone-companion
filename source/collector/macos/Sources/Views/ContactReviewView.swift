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
        }
    }

    private var controls: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: Brand.Space.item) {
                    includeAll
                    Divider().frame(height: 24)
                    filters()
                    Spacer(minLength: 0)
                    organizingToggle
                }
                VStack(alignment: .leading, spacing: Brand.Space.item) {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { includeAll; Spacer(minLength: 0); organizingToggle }
                        VStack(alignment: .leading, spacing: 8) { includeAll; organizingToggle }
                    }
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 6) { filters() }
                        HStack(spacing: 6) { filters(compact: true) }
                    }
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    searchField.frame(minWidth: 180, maxWidth: .infinity)
                    scopeActions
                }
                VStack(alignment: .leading, spacing: 8) {
                    searchField
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { scopeActions }
                        VStack(alignment: .leading, spacing: 8) { scopeActions }
                    }
                }
            }
            if showingGroupTools { organizingTools }
            if model.canUndoGroupMove {
                Button("Undo last group move") { model.undoContactMove() }
                    .buttonStyle(QuietAction())
            }
        }
        .brandControls()
    }

    private var includeAll: some View {
        Toggle(isOn: Binding(
            get: { !workspace.knownIDs.isEmpty && workspace.knownIDs.isSubset(of: model.selectedIDs) },
            set: { on in if on { model.selectAllVerifiedContacts() } else { model.clearAllVerifiedContacts() } }
        )) {
            Text("Include all \(workspace.knownIDs.count.formatted()) contacts")
                .font(Brand.bodyFont(14).weight(.bold))
                .foregroundStyle(Brand.heading)
        }
        .toggleStyle(BrandCheckbox(isMixed: !model.selectedIDs.isEmpty && !workspace.knownIDs.isSubset(of: model.selectedIDs)))
        .disabled(!model.canEditWorkspace)
        .accessibilityLabel("Include all \(workspace.knownIDs.count) contacts")
        .accessibilityValue("\(model.selectedIDs.count) selected across all contacts")
    }

    @ViewBuilder private func filters(compact: Bool = false) -> some View {
        ForEach(ContactGroup.allCases, id: \.rawValue) { group in
            let count = group == .all ? workspace.knownIDs.count : group == .main ? workspace.mainCount : workspace.reviewCount
            Button { model.showContactGroup(group) } label: {
                if compact {
                    Text("\(group.rawValue)\n\(count.formatted())").monospacedDigit().multilineTextAlignment(.center)
                } else {
                    HStack(spacing: 4) {
                        Text(group.rawValue)
                        Text(count.formatted()).monospacedDigit().fontWeight(.heavy)
                    }
                }
            }
            .buttonStyle(GroupChip(isSelected: workspace.group == group))
            .accessibilityAddTraits(workspace.group == group ? .isSelected : [])
            .help(group == .review
                  ? "Review holds contacts with a missing name, no supported phone number or an organization-like name. Your choice."
                  : "\(group.rawValue) contacts")
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(Brand.muted).accessibilityHidden(true)
            TextField("Search \(workspace.group.rawValue) by name", text: $search)
                .textFieldStyle(.plain)
                .font(Brand.bodyFont(15))
                .accessibilityLabel("Search contacts by name in \(workspace.group.rawValue)")
                .onChange(of: search) { _, value in
                    if value.unicodeScalars.count > 120 { search = String(value.unicodeScalars.prefix(120)) }
                    else { model.searchWorkspace(value) }
                }
            if !search.isEmpty {
                Button { search = "" } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain)
                    .foregroundStyle(Brand.muted)
                    .frame(width: 28, height: 28)
                    .accessibilityLabel("Clear contact search")
            }
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 44)
        .background(Brand.panel, in: Capsule())
        .overlay(Capsule().stroke(Brand.border, lineWidth: 1))
    }

    @ViewBuilder private var scopeActions: some View {
        Button("Select all \(workspace.filteredCount.formatted())") { model.selectScopedContacts() }
            .buttonStyle(QuietAction())
            .disabled(!model.canSelectScopedContacts)
            .accessibilityLabel("Select all \(workspace.filteredCount) contacts in \(workspace.scopeLabel)")
        Button("Deselect all") { model.clearScopedContacts() }
            .buttonStyle(QuietAction())
            .disabled(!model.canClearScopedContacts)
            .accessibilityLabel("Deselect all contacts in \(workspace.scopeLabel)")
        Text("\(model.selectedScopedCount.formatted()) of \(workspace.filteredCount.formatted()) selected")
            .font(Brand.bodyFont(13))
            .foregroundStyle(Brand.muted)
            .monospacedDigit()
            .accessibilityLabel("\(model.selectedScopedCount) of \(workspace.filteredCount) selected in \(workspace.scopeLabel)")
    }

    private var contactWell: some View {
        VStack(alignment: .leading, spacing: Brand.Space.item) {
            if workspace.filteredCount == 0 {
                VStack(alignment: .leading, spacing: 8) {
                    Text("No matches here. Your selections are kept. Clear the search or choose another group.")
                        .foregroundStyle(Brand.muted)
                    if !search.isEmpty {
                        Button("Clear search") { search = "" }.buttonStyle(QuietAction())
                    }
                }
                .frame(maxWidth: .infinity, minHeight: 120, alignment: .leading)
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: Brand.Layout.contactMinimum), spacing: 12)],
                              alignment: .leading, spacing: 12) {
                        ForEach(workspace.visible) { contact in
                            ContactReviewCard(model: model, workspace: workspace, contact: contact, organizing: showingGroupTools)
                        }
                    }
                }
                .frame(height: wellHeight)
                if workspace.hasPreviousPage || workspace.hasNextPage {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 12) { pagination }
                        VStack(alignment: .leading, spacing: 8) { pagination }
                    }
                }
            }
        }
        .brandWell()
        .id("\(workspace.group.rawValue)-\(workspace.query)-\(workspace.pageStart)")
    }

    @ViewBuilder private var pagination: some View {
        Text("Showing \(workspace.pageStart + 1)–\(workspace.pageStart + workspace.visible.count) of \(workspace.filteredCount.formatted())")
            .font(Brand.bodyFont(13))
            .foregroundStyle(Brand.muted)
        if workspace.hasPreviousPage {
            Button("Previous page") { model.previousWorkspacePage() }.buttonStyle(QuietAction())
        }
        if workspace.hasNextPage {
            Button("Next page") { model.nextWorkspacePage() }.buttonStyle(QuietAction())
        }
    }

    private var organizingToggle: some View {
        Button("\(showingGroupTools ? "Hide organizing" : "Organize groups") · \(model.markedGroupMoveIDs.count) marked") {
            showingGroupTools.toggle()
        }
        .buttonStyle(QuietAction())
    }

    private var organizingTools: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Marks are for moving groups, not selecting contacts to save. Moving to Review clears their selection; Undo restores it.")
                .font(Brand.bodyFont(13))
                .foregroundStyle(Brand.muted)
            if workspace.group == .all {
                Text("Choose Main or Review to mark a group move, or move one contact below.")
                    .font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            } else if workspace.filteredCount > 0 {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) { groupMoveActions }
                    VStack(alignment: .leading, spacing: 8) { groupMoveActions }
                }
            }
            DisclosureGroup("About Main and Review") {
                Text("Main starts with a display name and supported phone number. Review holds missing details or possible organization clues for your decision. One name is enough. These are local suggestions; nothing changes on your phone.")
                    .font(Brand.bodyFont(13)).foregroundStyle(Brand.muted)
            }
            .font(Brand.bodyFont(13))
        }
    }

    @ViewBuilder private var groupMoveActions: some View {
        let destination: ContactGroup = workspace.group == .main ? .review : .main
        Button("Move all \(workspace.filteredCount) matches to \(destination.rawValue)") {
            if model.beginBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton()).disabled(!model.canEditWorkspace)
        Button("Mark all \(workspace.filteredCount) matches") { model.markAllMatchingForGroupMove() }
            .buttonStyle(SecondaryButton()).disabled(!model.canEditWorkspace)
        Button("Move \(model.markedGroupMoveIDs.count) marked to \(destination.rawValue)") {
            if model.beginMarkedBulkMove(to: destination) { confirmingBulkMove = true }
        }
        .buttonStyle(SecondaryButton()).disabled(!model.canEditWorkspace || model.markedGroupMoveIDs.isEmpty)
        if !model.markedGroupMoveIDs.isEmpty {
            Button("Clear group marks") { model.clearGroupMoveMarks() }.buttonStyle(QuietAction())
        }
    }
}

private struct ContactReviewCard: View {
    @ObservedObject var model: CollectorModel
    let workspace: ContactWorkspace
    let contact: ContactPreview
    let organizing: Bool
    @State private var showingSourceDetails = false

    private var displayName: String { contact.name.isEmpty ? "Unnamed contact" : contact.name }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 8) {
                Toggle(isOn: Binding(get: { model.selectedIDs.contains(contact.id) }, set: { _ in model.toggle(contact.id) })) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(displayName).font(Brand.bodyFont(15).weight(.bold))
                            .foregroundStyle(Brand.heading).lineLimit(1).help(displayName)
                        Text(contact.phoneEnds.isEmpty ? "No supported phone number" : contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                            .font(Brand.bodyFont(13)).foregroundStyle(Brand.muted).lineLimit(1)
                        if let reason = workspace.reason(for: contact.id) {
                            Text(reviewTag(reason)).font(Brand.bodyFont(13).weight(.semibold))
                                .foregroundStyle(Brand.body).lineLimit(1).help(reason)
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(Brand.border, in: Capsule())
                                .accessibilityLabel(reason)
                        }
                    }
                    .frame(maxWidth: .infinity, minHeight: 52, alignment: .topLeading)
                }
                .toggleStyle(BrandCheckbox())
                .disabled(!model.canEditWorkspace)
                .accessibilityLabel("Include \(displayName) in selected contacts")
                Menu { secondaryActions } label: {
                    Image(systemName: "ellipsis").frame(width: 24, height: 24)
                }
                .menuStyle(.borderlessButton).menuIndicator(.hidden)
                .fixedSize()
                .accessibilityLabel("Actions and source details for \(displayName)")
                .popover(isPresented: $showingSourceDetails) { sourceDetails }
            }
            if organizing && workspace.group != .all {
                Toggle("Mark for group move", isOn: Binding(
                    get: { model.markedGroupMoveIDs.contains(contact.id) },
                    set: { _ in model.markContactForGroupMove(contact.id) }
                ))
                .toggleStyle(BrandCheckbox())
                .font(Brand.bodyFont(13))
                .disabled(!model.canEditWorkspace)
                .accessibilityLabel("Mark \(displayName) for group move")
            }
        }
        .brandRaisedCard(selected: model.selectedIDs.contains(contact.id))
        .contextMenu { secondaryActions }
    }

    @ViewBuilder private var secondaryActions: some View {
        Button(workspace.isInReview(contact.id) ? "Move to Main" : "Move to Review") {
            model.moveContact(contact.id, to: workspace.isInReview(contact.id) ? .main : .review)
        }
        .disabled(!model.canEditWorkspace)
        Button("Source details…") { showingSourceDetails = true }
    }

    private func reviewTag(_ reason: String) -> String {
        switch reason {
        case "Name is missing": return "Missing name"
        case "No supported phone number": return "No phone"
        case "Organization-like name marker": return "Organization?"
        case "Moved to Review by you": return "Moved to Review"
        default: return reason
        }
    }

    private var sourceDetails: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(displayName).font(Brand.headingFont(18)).foregroundStyle(Brand.heading)
            Text(contact.nameFieldDescription)
            Text("\(contact.phoneCount) supported phone numbers")
            if !contact.phoneEnds.isEmpty {
                Text(contact.phoneEnds.map { "•••• \($0)" }.joined(separator: " · "))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let reason = workspace.reason(for: contact.id) { Text(reason) }
            if contact.organizationField == true {
                Text("Organization field supplied. This is a hint, not verified business identity.")
            }
        }
        .font(Brand.bodyFont(13)).foregroundStyle(Brand.body)
        .padding(20).frame(width: 300, alignment: .leading)
        .background(Brand.panel)
        .textSelection(.enabled)
    }
}
