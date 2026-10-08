import Foundation

enum SupportDiagnosticReporter {
    private static let endpoint = URL(string: "https://amplifai-database-engine.vercel.app/api/v1/companion-diagnostics")!
    static let reportCodes: Set<String> = [
        "backup_host_space", "backup_password", "backup_stalled", "backup_no_file_progress", "backup_time_limit",
        "bridge_unavailable", "collection_failed", "connection_lost", "connection_timeout",
        "device_backup_failed", "phone_connection", "source_capacity_limit", "trust_required",
        "unsupported_schema", "workspace_cleanup", "workspace_low_space", "workspace_size_limit",
        "workspace_unavailable", "workspace_unsafe",
    ]

    static func request(for code: String) -> URLRequest? {
        let fields = code.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
        guard fields.count == 9, fields[0] == "A1", fields[1] == "26100805",
              fields[2].range(of: #"^[A-F0-9]{8}$"#, options: .regularExpression) != nil,
              ["connecting", "backup", "processing", "review"].contains(fields[3]),
              reportCodes.contains(fields[4]),
              (5...7).allSatisfy({ fields[$0].range(of: #"^[0-9]{1,16}$"#, options: .regularExpression) != nil }),
              fields[8] == "-" || fields[8].range(of: #"^-?[0-9]{1,11}$"#, options: .regularExpression) != nil,
              code.utf8.count <= 160,
              let body = try? JSONSerialization.data(withJSONObject: ["code": code]) else { return nil }
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 4)
        request.httpMethod = "POST"
        request.httpShouldHandleCookies = false
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        return request
    }

    static func send(_ code: String) async -> Bool {
        guard let request = request(for: code) else { return false }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 4
        configuration.timeoutIntervalForResource = 5
        configuration.httpShouldSetCookies = false
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        do {
            let (_, response) = try await session.data(for: request)
            return (response as? HTTPURLResponse)?.statusCode == 204 && response.url == endpoint
        } catch {
            return false
        }
    }
}
