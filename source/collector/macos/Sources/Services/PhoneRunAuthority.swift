import Foundation
import Security

/// The one-approval phone run, native side. The owner agrees once, in the signed-in AMPLIFai account, to this one
/// collection and to automatically saving the contacts they later choose. The website hands the app a short-lived
/// one-use bootstrap through an app link. The app redeems it here with a run capability it generates and keeps,
/// binds the helper's handoff after the owner chooses contacts, and checks the helper's pending connect intent
/// against the run before answering it.
///
/// Three identifiers stay distinct and are only ever associated, never reused for one another:
/// - the server run id (`runID`, with its `approvalID`) that the website created;
/// - the native per-run local collection approval id (`CollectorModel.collectionApprovalRunID`);
/// - the helper bridge `handoffId` for the chosen contacts.
///
/// Fixed TLS authority only: the same host the helper uses for connect intents, no browser cookie, no account id
/// and never a URL taken from a page, a link or a response.
enum PhoneRunContract {
    static let host = "amplifai-database-engine.vercel.app"
    static let claimPath = "/api/v1/phone-run-approvals/claim"
    static let bindPath = "/api/v1/phone-run-approvals/bind"
    static let verifyIntentPath = "/api/v1/phone-run-approvals/verify-intent"
    static let paths: Set<String> = [claimPath, bindPath, verifyIntentPath]
    /// The website's combined collection-and-save disclosure. Older collection-only receipts never stand in for it.
    static let disclosureVersion = "phone-run-v1"
    static let categories = ["contacts", "calls", "messages"]
    static let maximumResponseBytes = 2048
    static let maximumRequestBytes = 1024
    /// The run stays usable for 12 hours after the claim, far longer than the helper's five-minute pairing code,
    /// so a long collection can still bind. The server enforces it; the app only refuses to start a stale run.
    static let runLifetime: TimeInterval = 12 * 60 * 60

    static func isCapability(_ value: String) -> Bool { matches(value, #"\A[A-Za-z0-9_-]{43}\z"#, length: 43) }
    static func isID(_ value: String) -> Bool {
        matches(value, #"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z"#, length: 36)
    }
    static func isDigest(_ value: String) -> Bool { matches(value, #"\A[0-9a-f]{64}\z"#, length: 64) }

    private static func matches(_ value: String, _ pattern: String, length: Int) -> Bool {
        value.utf8.count == length && value.unicodeScalars.allSatisfy(\.isASCII) &&
            value.range(of: pattern, options: .regularExpression) != nil
    }

    static func isEmail(_ value: String) -> Bool {
        (3...254).contains(value.utf16.count) && value.contains("@") && !hasControlCharacter(value)
    }
    static func isCourse(_ value: String) -> Bool { (1...160).contains(value.utf16.count) && !hasControlCharacter(value) }
    private static func hasControlCharacter(_ value: String) -> Bool {
        value.unicodeScalars.contains { $0.value < 32 || $0.value == 127 }
    }

    /// 32 random bytes, base64url without padding: the same shape as every other one-use AMPLIFai capability.
    static func newCapability() -> String? {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { return nil }
        let value = Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        return isCapability(value) ? value : nil
    }

    /// Categories in canonical order: a non-empty, duplicate-free subset that always includes contacts.
    static func categories(_ raw: Any?) -> [String]? {
        guard let values = raw as? [Any], (1...3).contains(values.count) else { return nil }
        let strings = values.compactMap { $0 as? String }
        guard strings.count == values.count, Set(strings).count == strings.count,
              strings.allSatisfy(categories.contains), strings.contains("contacts") else { return nil }
        return categories.filter(strings.contains)
    }

    static func date(_ raw: Any?) -> Date? {
        guard let text = raw as? String, text.utf8.count <= 40 else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let whole = ISO8601DateFormatter()
        whole.formatOptions = [.withInternetDateTime]
        return fractional.date(from: text) ?? whole.date(from: text)
    }

    /// Strict object: within the size cap, exactly these keys, nothing else.
    static func object(_ body: Data?, keys: Set<String>) -> [String: Any]? {
        guard let body, body.count <= maximumResponseBytes,
              let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              Set(object.keys) == keys else { return nil }
        return object
    }

    static func claim(_ body: Data?) -> PhoneRunClaim? {
        guard let object = object(body, keys: ["approvalId", "enrollmentId", "destinationEmail", "destinationCourse",
                                               "categories", "disclosureVersion", "expiresAt"]),
              let approval = object["approvalId"] as? String, isID(approval),
              let enrollment = object["enrollmentId"] as? String, isID(enrollment),
              let email = object["destinationEmail"] as? String, isEmail(email),
              let course = object["destinationCourse"] as? String, isCourse(course),
              let categories = categories(object["categories"]),
              object["disclosureVersion"] as? String == disclosureVersion,
              let expiresAt = date(object["expiresAt"]) else { return nil }
        return PhoneRunClaim(approvalID: approval, enrollmentID: enrollment, email: email, course: course,
                             categories: categories, disclosureVersion: disclosureVersion, expiresAt: expiresAt)
    }

    static func destination(_ body: Data?) -> PhoneRunDestination? {
        guard let object = object(body, keys: ["enrollmentId", "destinationEmail", "destinationCourse", "categories"]),
              let enrollment = object["enrollmentId"] as? String, isID(enrollment),
              let email = object["destinationEmail"] as? String, isEmail(email),
              let course = object["destinationCourse"] as? String, isCourse(course),
              let categories = categories(object["categories"]) else { return nil }
        return PhoneRunDestination(enrollmentID: enrollment, email: email, course: course, categories: categories)
    }

    /// Fixed request bytes: prepared once, then sent unchanged on every attempt.
    static func body(_ fields: [String: String]) -> Data? {
        guard let data = try? JSONSerialization.data(withJSONObject: fields, options: [.sortedKeys]),
              data.count <= maximumRequestBytes else { return nil }
        return data
    }
}

/// What the server returned for the redeemed bootstrap.
struct PhoneRunClaim: Equatable {
    let approvalID: String
    let enrollmentID: String
    let email: String
    let course: String
    let categories: [String]
    let disclosureVersion: String
    let expiresAt: Date
}

/// What bind and verify-intent return: the account, course and categories the run currently saves to.
struct PhoneRunDestination: Equatable {
    let enrollmentID: String
    let email: String
    let course: String
    let categories: [String]
}

struct PhoneRunClaimRequest: Equatable {
    let runID: String
    let bootstrap: String
    let capability: String
    let body: Data

    init?(runID: String, bootstrap: String, capability: String) {
        guard PhoneRunContract.isID(runID), PhoneRunContract.isCapability(bootstrap),
              PhoneRunContract.isCapability(capability), capability != bootstrap,
              let body = PhoneRunContract.body(["runId": runID, "bootstrap": bootstrap, "capability": capability]) else { return nil }
        self.runID = runID
        self.bootstrap = bootstrap
        self.capability = capability
        self.body = body
    }
}

struct PhoneRunBindRequest: Equatable {
    let runID: String
    let handoffID: String
    let payloadSha256: String
    let body: Data

    init?(runID: String, capability: String, handoffID: String, payloadSha256: String) {
        guard PhoneRunContract.isID(runID), PhoneRunContract.isCapability(capability),
              PhoneRunContract.isID(handoffID), PhoneRunContract.isDigest(payloadSha256),
              let body = PhoneRunContract.body(["runId": runID, "capability": capability, "handoffId": handoffID,
                                                "payloadSha256": payloadSha256]) else { return nil }
        self.runID = runID
        self.handoffID = handoffID
        self.payloadSha256 = payloadSha256
        self.body = body
    }
}

struct PhoneRunVerifyRequest: Equatable {
    let runID: String
    let intentID: String
    let handoffID: String
    let payloadSha256: String
    let body: Data

    init?(runID: String, capability: String, intentID: String, handoffID: String, payloadSha256: String) {
        guard PhoneRunContract.isID(runID), PhoneRunContract.isCapability(capability), PhoneRunContract.isID(intentID),
              PhoneRunContract.isID(handoffID), PhoneRunContract.isDigest(payloadSha256),
              let body = PhoneRunContract.body(["runId": runID, "capability": capability, "intentId": intentID,
                                                "handoffId": handoffID, "payloadSha256": payloadSha256]) else { return nil }
        self.runID = runID
        self.intentID = intentID
        self.handoffID = handoffID
        self.payloadSha256 = payloadSha256
        self.body = body
    }
}

enum PhoneRunOutcome<Value: Equatable>: Equatable {
    /// A strictly parsed 200 reply.
    case accepted(Value)
    /// A definite refusal (4xx or any other non-success, non-5xx status): nothing was granted.
    case refused(status: Int, code: String?)
    /// No definite answer after the exact retries (transport failure or 5xx).
    case uncertain
    /// The success status kept arriving with an unreadable or malformed body. Never treated as success.
    case malformed
}

enum PhoneRunTransportResult: Equatable {
    /// `body` is nil when the reply was unreadable or exceeded the 2,048-byte cap.
    case response(status: Int, contentType: String?, body: Data?)
    case failed
}

@MainActor protocol PhoneRunTransport: AnyObject {
    func post(path: String, body: Data, completion: @escaping @MainActor (PhoneRunTransportResult) -> Void)
}

/// The injectable run authority the model uses. Production: `PhoneRunAuthorityClient.production()`.
@MainActor protocol RunAuthority: AnyObject {
    func claim(_ request: PhoneRunClaimRequest, completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunClaim>) -> Void)
    func bind(_ request: PhoneRunBindRequest, completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunDestination>) -> Void)
    func verifyIntent(_ request: PhoneRunVerifyRequest,
                      completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunDestination>) -> Void)
}

/// Sends fixed request bytes until there is a definite answer. A transport failure, a 5xx, or a 200 whose body is
/// unreadable or malformed is not an answer: the identical bytes are sent again, a bounded number of times. The
/// server reads an exact retry back and never treats it as a second grant. Any other status is a definite refusal.
@MainActor final class PhoneRunAuthorityClient: RunAuthority {
    typealias Schedule = (_ attempt: Int, _ work: @escaping @MainActor () -> Void) -> Void

    let transport: PhoneRunTransport
    let attempts: Int
    private let schedule: Schedule

    /// `schedule` nil: a short main-queue backoff between exact retries.
    init(transport: PhoneRunTransport, attempts: Int = 3, schedule: Schedule? = nil) {
        self.transport = transport
        self.attempts = max(1, attempts)
        self.schedule = schedule ?? Self.backoff
    }

    static func production() -> PhoneRunAuthorityClient { PhoneRunAuthorityClient(transport: URLSessionPhoneRunTransport()) }

    static let backoff: Schedule = { attempt, work in
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.75 * Double(attempt)) { MainActor.assumeIsolated { work() } }
    }
    /// Test and fixture seam: retry at once, synchronously.
    static let immediate: Schedule = { _, work in work() }

    func claim(_ request: PhoneRunClaimRequest, completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunClaim>) -> Void) {
        send(PhoneRunContract.claimPath, request.body, parse: PhoneRunContract.claim, attempt: 1, completion: completion)
    }

    func bind(_ request: PhoneRunBindRequest, completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunDestination>) -> Void) {
        send(PhoneRunContract.bindPath, request.body, parse: PhoneRunContract.destination, attempt: 1, completion: completion)
    }

    func verifyIntent(_ request: PhoneRunVerifyRequest,
                      completion: @escaping @MainActor (PhoneRunOutcome<PhoneRunDestination>) -> Void) {
        send(PhoneRunContract.verifyIntentPath, request.body, parse: PhoneRunContract.destination, attempt: 1,
             completion: completion)
    }

    private func send<Value>(_ path: String, _ body: Data, parse: @escaping (Data?) -> Value?, attempt: Int,
                             completion: @escaping @MainActor (PhoneRunOutcome<Value>) -> Void) {
        transport.post(path: path, body: body) { [weak self] result in
            guard let self else { completion(.uncertain); return }
            let malformed: Bool
            switch result {
            case .failed:
                malformed = false
            case let .response(status, contentType, data):
                if status == 200 {
                    if Self.isJSON(contentType), let value = parse(data) { completion(.accepted(value)); return }
                    malformed = true
                } else if status >= 500 && status <= 599 {
                    malformed = false
                } else {
                    completion(.refused(status: status, code: Self.problemCode(data)))
                    return
                }
            }
            guard attempt < self.attempts else { completion(malformed ? .malformed : .uncertain); return }
            self.schedule(attempt) { [weak self] in
                guard let self else { completion(.uncertain); return }
                self.send(path, body, parse: parse, attempt: attempt + 1, completion: completion)
            }
        }
    }

    static func isJSON(_ contentType: String?) -> Bool {
        guard let type = contentType?.lowercased().trimmingCharacters(in: .whitespaces),
              type.hasPrefix("application/json") else { return false }
        let rest = type.dropFirst("application/json".count)
        return rest.isEmpty || rest.hasPrefix(";")
    }

    /// Best effort: a bounded problem code from a refusal body, for wording only, never for authority.
    static func problemCode(_ body: Data?) -> String? {
        guard let body, body.count <= PhoneRunContract.maximumResponseBytes,
              let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              let code = object["code"] as? String, code.utf8.count <= 64,
              code.range(of: #"\A[a-z_]+\z"#, options: .regularExpression) != nil else { return nil }
        return code
    }
}

/// Production transport: HTTPS POST to the fixed host on an ephemeral session with no cookies, cache or credential
/// storage. Redirects are refused, the reply must be JSON and is capped at 2,048 bytes.
@MainActor final class URLSessionPhoneRunTransport: PhoneRunTransport {
    nonisolated static func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieAcceptPolicy = .never
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 20
        configuration.waitsForConnectivity = false
        configuration.httpMaximumConnectionsPerHost = 1
        configuration.tlsMinimumSupportedProtocolVersion = .TLSv12
        return configuration
    }

    /// Only the three fixed paths on the fixed host can ever be requested.
    nonisolated static func request(path: String, body: Data) -> URLRequest? {
        guard PhoneRunContract.paths.contains(path), body.count <= PhoneRunContract.maximumRequestBytes else { return nil }
        var components = URLComponents()
        components.scheme = "https"
        components.host = PhoneRunContract.host
        components.path = path
        guard let url = components.url else { return nil }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 10)
        request.httpMethod = "POST"
        request.httpBody = body
        request.httpShouldHandleCookies = false
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    func post(path: String, body: Data, completion: @escaping @MainActor (PhoneRunTransportResult) -> Void) {
        guard let request = Self.request(path: path, body: body) else { completion(.failed); return }
        let delegate = PhoneRunRequestDelegate(completion: completion)
        let session = URLSession(configuration: Self.configuration(), delegate: delegate, delegateQueue: .main)
        session.dataTask(with: request).resume()
        session.finishTasksAndInvalidate()
    }
}

/// One request's delegate. Its queue is the main queue, so every callback runs on the main thread.
private final class PhoneRunRequestDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let completion: @MainActor (PhoneRunTransportResult) -> Void
    private var status = 0
    private var contentType: String?
    private var body = Data()
    private var oversized = false
    private var finished = false

    init(completion: @escaping @MainActor (PhoneRunTransportResult) -> Void) {
        self.completion = completion
    }

    private func trustOnly(_ challenge: URLAuthenticationChallenge,
                           _ handler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        // Default TLS server-trust evaluation only; never answer an HTTP credential challenge.
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           challenge.protectionSpace.host == PhoneRunContract.host {
            handler(.performDefaultHandling, nil)
        } else {
            handler(.cancelAuthenticationChallenge, nil)
        }
    }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        trustOnly(challenge, completionHandler)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        trustOnly(challenge, completionHandler)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil) // Never follow: the redirect itself becomes the (refused) answer.
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse, http.url?.host == PhoneRunContract.host else {
            completionHandler(.cancel)
            return
        }
        status = http.statusCode
        contentType = http.value(forHTTPHeaderField: "Content-Type")
        if http.expectedContentLength > Int64(PhoneRunContract.maximumResponseBytes) {
            oversized = true
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard !oversized else { return }
        body.append(data)
        if body.count > PhoneRunContract.maximumResponseBytes {
            oversized = true
            body = Data()
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard !finished else { return }
        finished = true
        let result: PhoneRunTransportResult
        if status == 0 { result = .failed }
        else if oversized { result = .response(status: status, contentType: contentType, body: nil) }
        else if error != nil { result = .failed }
        else { result = .response(status: status, contentType: contentType, body: body) }
        let completion = self.completion
        MainActor.assumeIsolated { completion(result) }
    }
}
