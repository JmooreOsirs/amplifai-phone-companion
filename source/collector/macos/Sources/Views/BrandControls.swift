import SwiftUI

struct BrandCheckbox: ToggleStyle {
    var isMixed = false

    func makeBody(configuration: Configuration) -> some View {
        Toggle(configuration)
            .toggleStyle(.button)
            .buttonStyle(BrandCheckboxButton(isOn: configuration.isOn, isMixed: isMixed))
            .focusable()
            .accessibilityValue(Text(isMixed ? "Mixed" : (configuration.isOn ? "On" : "Off")))
    }
}

private struct BrandCheckboxButton: ButtonStyle {
    let isOn: Bool
    let isMixed: Bool
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused

    func makeBody(configuration: Configuration) -> some View {
        HStack(alignment: .top, spacing: Brand.Space.item) {
            ZStack {
                RoundedRectangle(cornerRadius: Brand.Radius.checkbox)
                    .fill(isOn && !isMixed ? Brand.lime : Brand.background)
                RoundedRectangle(cornerRadius: Brand.Radius.checkbox)
                    .strokeBorder(isMixed ? Brand.blue : (isOn ? Brand.lime : Brand.muted), lineWidth: 1.5)
                if isMixed || isOn {
                    Image(systemName: isMixed ? "minus" : "checkmark")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(isMixed ? Brand.blue : Brand.background)
                }
            }
            .frame(width: Brand.Control.checkboxSize, height: Brand.Control.checkboxSize)
            .accessibilityHidden(true)
            configuration.label
        }
        .font(Brand.bodyFont(14))
        .foregroundStyle(Brand.body)
        .frame(minHeight: Brand.Control.compactHeight, alignment: .leading)
        .contentShape(Rectangle())
        .opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : Brand.Control.disabledOpacity)
        .modifier(BrandFocusRing(isFocused: isFocused && isEnabled, radius: Brand.Radius.control))
    }
}

struct GroupChip: ButtonStyle {
    let isSelected: Bool

    func makeBody(configuration: Configuration) -> some View {
        GroupChipBody(configuration: configuration, isSelected: isSelected)
    }
}

private struct GroupChipBody: View {
    let configuration: ButtonStyleConfiguration
    let isSelected: Bool
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused
    @State private var isHovered = false

    var body: some View {
        configuration.label
            .font(Brand.headingFont(13))
            .monospacedDigit()
            .foregroundStyle(isSelected ? Brand.background : Brand.heading)
            .padding(.horizontal, 12)
            .padding(.vertical, 4)
            .frame(minHeight: Brand.Control.compactHeight)
            .background(isSelected ? Brand.lime : Brand.background, in: Capsule())
            .overlay(Capsule().strokeBorder(isSelected || (isHovered && isEnabled) ? Brand.lime : Brand.border, lineWidth: 1))
            .contentShape(Capsule())
            .opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : Brand.Control.disabledOpacity)
            .modifier(BrandFocusRing(isFocused: isFocused && isEnabled, radius: Brand.Control.compactHeight))
            .onHover { isHovered = $0 }
    }
}

struct QuietAction: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        QuietActionBody(configuration: configuration)
    }
}

private struct QuietActionBody: View {
    let configuration: ButtonStyleConfiguration
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.isFocused) private var isFocused
    @State private var isHovered = false

    var body: some View {
        configuration.label
            .font(Brand.headingFont(13))
            .underline()
            .foregroundStyle(isHovered && isEnabled ? Brand.heading : Brand.muted)
            .frame(minHeight: Brand.Control.compactHeight)
            .contentShape(Rectangle())
            .opacity(isEnabled ? (configuration.isPressed ? 0.8 : 1) : Brand.Control.disabledOpacity)
            .modifier(BrandFocusRing(isFocused: isFocused && isEnabled, radius: Brand.Radius.control))
            .onHover { isHovered = $0 }
    }
}

private struct BrandFocusRing: ViewModifier {
    let isFocused: Bool
    let radius: CGFloat

    func body(content: Content) -> some View {
        content.overlay {
            if isFocused {
                RoundedRectangle(cornerRadius: radius)
                    .stroke(Brand.blue, lineWidth: Brand.Control.focusWidth)
                    .padding(-(Brand.Control.focusGap + Brand.Control.focusWidth / 2))
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
    }
}

private struct BrandControlsSurface: ViewModifier {
    let padding: CGFloat

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Brand.background, in: RoundedRectangle(cornerRadius: Brand.Radius.card))
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .strokeBorder(Brand.border, lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

private struct BrandWell: ViewModifier {
    let padding: CGFloat

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .fill(Brand.background.shadow(.inner(color: Brand.Depth.wellShadow, radius: 10, y: 2)))
            }
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .strokeBorder(Brand.border, lineWidth: 1)
                    .allowsHitTesting(false)
            }
    }
}

private struct BrandRaisedCard: ViewModifier {
    let selected: Bool
    let padding: CGFloat

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .fill(Brand.raisedSurface)
                    .shadow(color: Brand.Depth.cardShadow, radius: 8, y: 3)
            }
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .inset(by: 1)
                    .strokeBorder(Brand.Depth.cardHighlight, lineWidth: 1)
                    .mask(LinearGradient(colors: [.white, .clear], startPoint: .top, endPoint: .bottom))
                    .allowsHitTesting(false)
            }
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.card)
                    .strokeBorder(selected ? Brand.lime : Brand.border, lineWidth: 1)
                    .allowsHitTesting(false)
            }
            .overlay {
                if selected {
                    RoundedRectangle(cornerRadius: Brand.Radius.card + 1)
                        .stroke(Brand.Depth.selectionHalo, lineWidth: 1)
                        .padding(-1)
                        .allowsHitTesting(false)
                }
            }
    }
}

/// Agree · Collect · Choose · Share & report. Finished steps use a filled check-circle, never a checkbox,
/// so verified progress cannot be mistaken for a choice.
struct JourneySteps: View {
    static let titles = ["Approve", "Collect", "Choose", "Save & report"]
    /// 0...3 is the current step; 4 means every step finished.
    let current: Int

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 8) { steps(compact: false) }
            HStack(spacing: 6) { steps(compact: true) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(current < Self.titles.count
                            ? "Step \(current + 1) of \(Self.titles.count): \(Self.titles[current])"
                            : "All \(Self.titles.count) steps finished")
    }

    @ViewBuilder private func steps(compact: Bool) -> some View {
        ForEach(Self.titles.indices, id: \.self) { index in
            HStack(spacing: 6) {
                if index < current {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundStyle(Brand.lime)
                } else {
                    Text("\(index + 1)")
                        .font(Brand.headingFont(12))
                        .foregroundStyle(index == current ? Brand.background : Brand.muted)
                        .frame(width: 20, height: 20)
                        .background(index == current ? Brand.lime : Brand.background, in: Circle())
                        .overlay(Circle().stroke(index == current ? Brand.lime : Brand.border, lineWidth: 1))
                }
                if !compact || index == current {
                    Text(Self.titles[index])
                        .font(Brand.headingFont(13))
                        .foregroundStyle(index == current ? Brand.heading : Brand.muted)
                        .lineLimit(1)
                }
            }
            if index < Self.titles.count - 1 {
                Rectangle().fill(Brand.border).frame(width: compact ? 10 : 24, height: 1)
            }
        }
    }
}

/// A read result from the phone or helper. Icons, not checkboxes: nothing here is the owner's choice.
struct VerifiedStatusRow: View {
    let verified: Bool
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: verified ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .font(.system(size: 18))
                .foregroundStyle(verified ? Brand.lime : Brand.muted)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(Brand.headingFont(16)).foregroundStyle(Brand.heading)
                Text(detail).font(Brand.bodyFont(15)).foregroundStyle(Brand.body)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Organizing mark for a Main/Review group move. A flag tag, deliberately unlike the include checkbox.
struct MoveMarkToggle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 6) {
                Image(systemName: configuration.isOn ? "flag.fill" : "flag")
                configuration.label
            }
            .font(Brand.headingFont(12))
            .foregroundStyle(configuration.isOn ? Brand.heading : Brand.muted)
            .padding(.horizontal, 10)
            .frame(minHeight: 26)
            .background(configuration.isOn ? Brand.blue.opacity(0.35) : Brand.background, in: Capsule())
            .overlay(Capsule().strokeBorder(configuration.isOn ? Brand.blue : Brand.border,
                                            style: StrokeStyle(lineWidth: 1, dash: configuration.isOn ? [] : [3, 2])))
        }
        .buttonStyle(.plain)
        .accessibilityValue(Text(configuration.isOn ? "Marked to move" : "Not marked"))
    }
}

extension View {
    func brandControls(padding: CGFloat = 16) -> some View {
        modifier(BrandControlsSurface(padding: padding))
    }

    func brandWell(padding: CGFloat = 12) -> some View {
        modifier(BrandWell(padding: padding))
    }

    func brandRaisedCard(selected: Bool = false, padding: CGFloat = 12) -> some View {
        modifier(BrandRaisedCard(selected: selected, padding: padding))
    }
}
