import CryptoKit
import Foundation

/// FAKE DATA: the real helper bridge's phase order (amplifai_phone/bridge.py and agent.py, unchanged), shared by
/// the fake helper and its checks so the demo can never report a phase early.
///
/// pair → browser connect (/v3/connect: destination pending) → app decision → browser release (/v3/release:
/// token and manifest; handoff_state() is still "waiting" because used is false) → every /v2/source page read →
/// website saves → /v1/complete (used = true: only now "received") → /v1/acknowledge-save
/// ("saved_pending_browser_receipt") → /v1/confirm-ack-received ("saved") → app sends finish.
///
/// Clock rules, as in bridge.py: the five-minute window (PAIR_SECONDS) starts when `pair` creates the bridge, after
/// review and Continue, never at the claim or during collection. Connect, status, the decision and release need
/// now < pair deadline. Release sets the transfer deadline to now + two hours (TRANSFER_SECONDS); every page,
/// keepalive, complete and save acknowledgement needs now < transfer deadline and refreshes it. An unreleased
/// bridge closes at the pair deadline.
struct FakeBridgeTimeline: Equatable {
    static let pairSeconds: TimeInterval = 300
    static let transferSeconds: TimeInterval = 2 * 60 * 60

    enum Step: Equatable {
        case linked, pending, denied, approved, released, pagesDelivered, complete, acknowledged, confirmed, expired
    }
    /// What the simulated website does, in its only valid order. `page` is one more source page (or keepalive)
    /// while pages are still being read; `pages` is the last one.
    enum WebsiteAction: String, CaseIterable {
        case connect, release, page, pages, complete, acknowledge, confirm
    }

    private(set) var step: Step = .linked
    /// Each browser connect creates a new intent.
    private(set) var connectAttempt = 0
    let pairedAt: TimeInterval
    private(set) var transferDeadline: TimeInterval?
    var pairDeadline: TimeInterval { pairedAt + Self.pairSeconds }

    init(pairedAt: TimeInterval = 0) { self.pairedAt = pairedAt }

    /// Closes the bridge when its current deadline has passed (handoff_state() then reports expired).
    @discardableResult
    mutating func expireIfDue(at now: TimeInterval) -> Bool {
        guard ![.acknowledged, .confirmed, .expired].contains(step) else { return false }
        if let transferDeadline { guard now >= transferDeadline else { return false } }
        else { guard now >= pairDeadline else { return false } }
        step = .expired
        return true
    }

    /// Applies a website action only where, and while, the bridge accepts it.
    @discardableResult
    mutating func apply(_ action: WebsiteAction, at now: TimeInterval) -> Bool {
        if expireIfDue(at: now) { return false }
        switch (action, step) {
        case (.connect, .linked), (.connect, .denied):
            guard transferDeadline == nil, now < pairDeadline else { return false }
            step = .pending
            connectAttempt += 1
        case (.release, .approved):
            guard now < pairDeadline else { return false }
            step = .released
            transferDeadline = now + Self.transferSeconds
        case (.page, .released):
            transferDeadline = now + Self.transferSeconds
        case (.pages, .released):
            step = .pagesDelivered
            transferDeadline = now + Self.transferSeconds
        case (.complete, .pagesDelivered):
            step = .complete
            transferDeadline = now + Self.transferSeconds
        case (.acknowledge, .complete):
            step = .acknowledged
            transferDeadline = now + Self.transferSeconds
        case (.confirm, .acknowledged):
            step = .confirmed
        default: return false
        }
        return true
    }

    /// The app's destination-decision; only a pending intent inside the pair window can be decided.
    @discardableResult
    mutating func decide(approved: Bool, at now: TimeInterval) -> Bool {
        if expireIfDue(at: now) { return false }
        guard step == .pending, now < pairDeadline else { return false }
        step = approved ? .approved : .denied
        return true
    }

    var released: Bool { [.released, .pagesDelivered, .complete, .acknowledged, .confirmed].contains(step) }

    /// bridge.handoff_state(): received only once used is true, after /v1/complete.
    var handoffState: String {
        switch step {
        case .confirmed: return "saved"
        case .acknowledged: return "saved_pending_browser_receipt"
        case .complete: return "received"
        case .expired: return "expired"
        default: return "waiting"
        }
    }

    var destinationPending: Bool { step == .pending }

    /// The next website action the bridge would accept, if any.
    var nextWebsiteAction: WebsiteAction? {
        switch step {
        case .linked, .denied: return .connect
        case .approved: return .release
        case .released: return .pages
        case .pagesDelivered: return .complete
        case .complete: return .acknowledge
        case .acknowledged: return .confirm
        default: return nil
        }
    }

    /// The fictional intent the simulated website created for this connect.
    static func intentID(forHandoff handoffID: String, attempt: Int) -> String {
        let digest = Array(SHA256.hash(data: Data("fake-intent:\(handoffID):\(attempt)".utf8)))
        var bytes = Array(digest.prefix(16))
        bytes[6] = (bytes[6] & 0x0f) | 0x40
        bytes[8] = (bytes[8] & 0x3f) | 0x80
        let hex = bytes.map { String(format: "%02x", $0) }.joined()
        let parts = [hex.prefix(8), hex.dropFirst(8).prefix(4), hex.dropFirst(12).prefix(4),
                     hex.dropFirst(16).prefix(4), hex.dropFirst(20)]
        return parts.map(String.init).joined(separator: "-")
    }

    /// The fictional manifest digest for the chosen contacts.
    static func payloadDigest(_ ids: [Int]) -> String {
        var digest = SHA256()
        digest.update(data: Data("fake-manifest\n".utf8))
        for id in ids { digest.update(data: Data("\(id)\n".utf8)) }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
