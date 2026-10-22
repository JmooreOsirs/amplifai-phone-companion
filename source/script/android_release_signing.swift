#!/usr/bin/env swift
// macOS-only local signing custody. Never copy the keystore into the repository.
// Usage: swift script/android_release_signing.swift create | info | build | build-play
//        swift script/android_release_signing.swift sign-feed APK HTTPS_RELEASE_URL OUTPUT
//        swift script/android_release_signing.swift sign-test INSTRUMENTATION_APK OUTPUT
// `build` is a separate release action: run only after reviewing the Android changes.
// Back up the private key/password only through a separately approved secure process.

import CryptoKit
import Darwin
import Foundation
import LocalAuthentication
import Security

enum SigningError: Error, CustomStringConvertible {
    case unsafe(String)
    var description: String {
        switch self { case .unsafe(let message): return message }
    }
}

let service = "ai.satoris.amplifai.android-release"
let keyAlias = "amplifai-android-release"
let javaHome = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
let keytool = javaHome + "/bin/keytool"
let passwordVariable = "AMPLIFAI_ANDROID_STORE_PASSWORD"
let fileManager = FileManager.default

func fail(_ message: String) throws -> Never { throw SigningError.unsafe(message) }

func ownerHome() throws -> String {
    guard getuid() != 0, let record = getpwuid(getuid()), let directory = record.pointee.pw_dir else {
        try fail("Run as the signed-in non-root owner.")
    }
    return String(cString: directory)
}

func metadata(_ path: String) throws -> stat? {
    var result = stat()
    if lstat(path, &result) == 0 { return result }
    guard errno == ENOENT else { try fail("Cannot inspect signing path: \(path)") }
    return nil
}

func requirePrivatePath(_ path: String, directory: Bool) throws {
    guard let value = try metadata(path), value.st_uid == getuid(),
          value.st_mode & S_IFMT == (directory ? S_IFDIR : S_IFREG),
          value.st_mode & 0o777 == (directory ? 0o700 : 0o600),
          directory || value.st_nlink == 1 else {
        try fail("Signing path must be owner-held, non-symlink, and \(directory ? "0700" : "0600"): \(path)")
    }
}

func signingDirectory(home: String, create: Bool) throws -> String {
    var path = home
    for component in [".config", "amplifai", "signing"] {
        path += "/" + component
        if try metadata(path) == nil {
            guard create, mkdir(path, 0o700) == 0 else { try fail("Missing or unavailable signing directory: \(path)") }
        }
        guard let value = try metadata(path), value.st_uid == getuid(), value.st_mode & S_IFMT == S_IFDIR else {
            try fail("Refusing a foreign-owned or symlinked signing directory: \(path)")
        }
    }
    try requirePrivatePath(path, directory: true)
    return path
}

func loginKeychain(home: String) throws -> SecKeychain {
    // Explicit login-keychain custody requires this deprecated file-keychain API;
    // switching to the modern data-protection keychain would change the ask.
    var keychain: SecKeychain?
    let status = SecKeychainOpen(home + "/Library/Keychains/login.keychain-db", &keychain)
    guard status == errSecSuccess, let keychain else {
        try fail("Cannot open the login Keychain (status \(status)); no other keychain will be used.")
    }
    return keychain
}

func credentialQuery(_ keychain: SecKeychain) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword,
     kSecAttrService as String: service,
     kSecMatchSearchList as String: [keychain],
     kSecMatchLimit as String: kSecMatchLimitOne]
}

func requireNoCredential(_ keychain: SecKeychain) throws {
    let status = SecItemCopyMatching(credentialQuery(keychain) as CFDictionary, nil)
    guard status == errSecItemNotFound else {
        if status == errSecSuccess { try fail("Dedicated signing credential already exists; refusing replacement.") }
        try fail("Cannot safely check the dedicated signing credential (status \(status)).")
    }
}

func readPassword(_ keychain: SecKeychain, allowPrompt: Bool = true) throws -> String {
    var query = credentialQuery(keychain)
    query[kSecAttrAccount as String] = keyAlias
    query[kSecReturnData as String] = true
    let noPromptContext = allowPrompt ? nil : LAContext()
    noPromptContext?.interactionNotAllowed = true
    if let noPromptContext { query[kSecUseAuthenticationContext as String] = noPromptContext }
    var item: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &item)
    guard status == errSecSuccess, let data = item as? Data,
          let password = String(data: data, encoding: .utf8), password.count == 64 else {
        try fail("Cannot retrieve the dedicated signing password (status \(status)); unlock login Keychain if needed.")
    }
    return password
}

func newPassword(_ keychain: SecKeychain) throws -> String {
    var bytes = [UInt8](repeating: 0, count: 48)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
        try fail("Secure random generation failed; nothing was created.")
    }
    let password = Data(bytes).base64EncodedString()
    let attributes: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: service,
        kSecAttrAccount as String: keyAlias,
        kSecAttrLabel as String: "AMPLIFai Android release signing",
        kSecAttrDescription as String: "Local Android release keystore and key password; preserve for app updates.",
        kSecValueData as String: Data(password.utf8),
        kSecUseKeychain as String: keychain,
    ]
    let status = SecItemAdd(attributes as CFDictionary, nil)
    guard status == errSecSuccess else { try fail("Could not add the dedicated credential (status \(status)); no key was generated.") }
    guard try readPassword(keychain) == password else { try fail("Keychain readback failed; no key was generated.") }
    return password
}

func childEnvironment(home: String, password: String) -> [String: String] {
    // Do not inherit Java injection/debug options, shell variables, or unrelated secrets.
    ["HOME": home, "PATH": javaHome + "/bin:/usr/bin:/bin:/usr/sbin:/sbin",
     "JAVA_HOME": javaHome, "LANG": "en_US.UTF-8", "TMPDIR": "/tmp",
     passwordVariable: password]
}

func run(_ executable: String, arguments: [String], environment: [String: String],
         directory: String? = nil, publicOutput: Bool = false) throws -> Data {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.environment = environment
    if let directory { process.currentDirectoryURL = URL(fileURLWithPath: directory) }
    process.standardInput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    let output = Pipe()
    process.standardOutput = publicOutput ? output : FileHandle.nullDevice
    do { try process.run() } catch { try fail("Could not launch \(URL(fileURLWithPath: executable).lastPathComponent).") }
    // Only keytool's public certificate/listing commands may return output. Gradle
    // output is suppressed because build plugins can accidentally log credentials.
    let data = publicOutput ? output.fileHandleForReading.readDataToEndOfFile() : Data()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        try fail("\(URL(fileURLWithPath: executable).lastPathComponent) failed (exit \(process.terminationStatus)); child output is withheld to protect credentials. Existing key/credential material was preserved.")
    }
    return data
}

func keytoolArguments(_ operation: String, path: String) -> [String] {
    ["-J-Duser.language=en", "-J-Duser.country=US", operation, "-keystore", path,
     "-storetype", "PKCS12", "-alias", keyAlias, "-storepass:env", passwordVariable]
}

func printPublicIdentity(path: String, environment: [String: String]) throws {
    try requirePrivatePath(path, directory: false)
    let listing = try run(keytool, arguments: keytoolArguments("-list", path: path) + ["-v"],
                          environment: environment, publicOutput: true)
    guard let description = String(data: listing, encoding: .utf8),
          description.contains("Entry type: PrivateKeyEntry"),
          description.contains("Certificate chain length: 1"),
          description.contains("3072-bit RSA key"),
          description.contains("Signature algorithm name: SHA256withRSA"),
          description.contains("Owner: CN=AMPLIFai Android Release, O=OSIRS LLC"),
          description.contains("Issuer: CN=AMPLIFai Android Release, O=OSIRS LLC") else {
        try fail("The dedicated keystore does not match the expected self-signed RSA-3072/SHA-256 identity.")
    }
    let certificate = try run(keytool, arguments: keytoolArguments("-exportcert", path: path),
                              environment: environment, publicOutput: true)
    guard SecCertificateCreateWithData(nil, certificate as CFData) != nil else { try fail("Invalid public certificate output.") }
    let fingerprint = SHA256.hash(data: certificate).map { String(format: "%02X", $0) }.joined(separator: ":")
    print("Keystore: \(path)\nAlias: \(keyAlias)\nSHA-256: \(fingerprint)")
}

func createIdentity(home: String, directory: String, path: String, keychain: SecKeychain) throws {
    let lock = directory + "/.creation-lock"
    guard mkdir(lock, 0o700) == 0 else { try fail("Signing creation is already locked; inspect before retrying.") }
    defer { if rmdir(lock) != 0 { fputs("Creation lock could not be removed; inspect before retrying.\n", stderr) } }
    guard try metadata(path) == nil else { try fail("Dedicated keystore already exists; refusing replacement.") }
    try requireNoCredential(keychain)
    let password = try newPassword(keychain)
    let environment = childEnvironment(home: home, password: password)
    let arguments = keytoolArguments("-genkeypair", path: path) + [
        "-keypass:env", passwordVariable, "-keyalg", "RSA", "-keysize", "3072",
        "-sigalg", "SHA256withRSA", "-validity", "10000", "-dname",
        "CN=AMPLIFai Android Release,O=OSIRS LLC", "-noprompt",
    ]
    _ = try run(keytool, arguments: arguments, environment: environment)
    try printPublicIdentity(path: path, environment: environment)
}

func buildRelease(home: String, path: String, password: String, play: Bool = false) throws {
    let root = URL(fileURLWithPath: #filePath).standardizedFileURL.deletingLastPathComponent().deletingLastPathComponent()
    let android = root.appendingPathComponent("collector/android").path
    let configuration = android + "/app/build.gradle.kts"
    guard let buildScript = try? String(contentsOfFile: configuration, encoding: .utf8),
          ["AMPLIFAI_ANDROID_KEYSTORE_PATH", "AMPLIFAI_ANDROID_KEY_ALIAS",
           "AMPLIFAI_ANDROID_STORE_PASSWORD", "AMPLIFAI_ANDROID_KEY_PASSWORD"].allSatisfy(buildScript.contains) else {
        try fail("Android release signing configuration has not been integrated; no build was started.")
    }
    let sdk = home + "/Library/Android/sdk"
    guard fileManager.isExecutableFile(atPath: android + "/gradlew"), fileManager.fileExists(atPath: sdk) else {
        try fail("Android Gradle wrapper or local Android SDK is unavailable; no build was started.")
    }
    var environment = childEnvironment(home: home, password: password)
    environment["ANDROID_HOME"] = sdk
    environment["ANDROID_SDK_ROOT"] = sdk
    environment["AMPLIFAI_ANDROID_KEYSTORE_PATH"] = path
    environment["AMPLIFAI_ANDROID_KEY_ALIAS"] = keyAlias
    environment["AMPLIFAI_ANDROID_KEY_PASSWORD"] = password
    let task = play ? "bundlePlayRelease" : "assembleWebsiteRelease"
    _ = try run(android + "/gradlew", arguments: ["--no-configuration-cache", "--no-daemon", task],
                environment: environment, directory: android)
    print("\(play ? "Play AAB" : "Website APK") build finished locally. Verify the exact artifact signature and certificate before any distribution.")
}

func capture(_ pattern: String, in text: String) throws -> [String] {
    let expression = try NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines])
    let range = NSRange(text.startIndex..<text.endIndex, in: text)
    guard let match = expression.firstMatch(in: text, range: range) else { try fail("APK metadata did not match the release contract.") }
    return (1..<match.numberOfRanges).compactMap { index in
        Range(match.range(at: index), in: text).map { String(text[$0]) }
    }
}

func signFeed(path: String, password: String, apk: String, releaseURL: String, output: String,
              environment: [String: String]) throws {
    guard let apkStat = try metadata(apk), apkStat.st_mode & S_IFMT == S_IFREG,
          apkStat.st_uid == getuid(), apkStat.st_size > 0, apkStat.st_size <= 32 * 1024 * 1024 else {
        try fail("APK must be an owner-held regular file within the signed update limit.")
    }
    let sdkBuildTools = try ownerHome() + "/Library/Android/sdk/build-tools/36.0.0"
    let signerOutput = try run(sdkBuildTools + "/apksigner", arguments: ["verify", "--verbose", "--print-certs", apk],
                               environment: environment, publicOutput: true)
    guard let signerText = String(data: signerOutput, encoding: .utf8),
          signerText.contains("Signer #1 certificate SHA-256 digest: 9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2"),
          signerText.contains("Number of signers: 1") else { try fail("APK signature is not the pinned publisher identity.") }
    let badge = try run(sdkBuildTools + "/aapt", arguments: ["dump", "badging", apk],
                        environment: environment, publicOutput: true)
    guard let badgeText = String(data: badge, encoding: .utf8) else { try fail("APK metadata is unreadable.") }
    let values = try capture("^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", in: badgeText)
    let sdk = try capture("^sdkVersion:'([0-9]+)'", in: badgeText)
    guard values.count == 3, sdk.count == 1, values[0] == "ai.satoris.amplifai.phone",
          values[1].range(of: "^[1-9][0-9]{0,8}$", options: .regularExpression) != nil,
          values[2].range(of: "^20[0-9]{2}\\.[0-9]{2}\\.[0-9]{2}-rc[1-9][0-9]{0,2}$", options: .regularExpression) != nil,
          sdk[0].range(of: "^[1-9][0-9]{0,2}$", options: .regularExpression) != nil else {
        try fail("APK package/version/SDK is outside the signed feed contract.")
    }
    let expectedURL = "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/android-\(values[2])/AMPLIFai-Phone-Android-\(values[2]).apk"
    guard releaseURL == expectedURL else { try fail("Release URL does not match the immutable tag and APK version.") }
    let apkData = try Data(contentsOf: URL(fileURLWithPath: apk))
    let digest = SHA256.hash(data: apkData).map { String(format: "%02x", $0) }.joined()
    let body = """
    AMPLIFAI_ANDROID_UPDATE_V1
    package=ai.satoris.amplifai.phone
    versionCode=\(values[1])
    versionName=\(values[2])
    minSdk=\(sdk[0])
    apkBytes=\(apkStat.st_size)
    apkSha256=\(digest)
    signerSha256=9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2
    apkUrl=\(releaseURL)

    """
    var imported: CFArray?
    let options = [kSecImportExportPassphrase as String: password]
    guard SecPKCS12Import(try Data(contentsOf: URL(fileURLWithPath: path)) as CFData,
                          options as CFDictionary, &imported) == errSecSuccess,
          let items = imported as? [[String: Any]], items.count == 1,
          let rawIdentity = items[0][kSecImportItemIdentity as String],
          CFGetTypeID(rawIdentity as CFTypeRef) == SecIdentityGetTypeID() else {
        try fail("The existing release identity could not sign the update feed.")
    }
    let identity = rawIdentity as! SecIdentity
    var privateKey: SecKey?
    guard SecIdentityCopyPrivateKey(identity, &privateKey) == errSecSuccess,
          let privateKey else { try fail("The existing release private key is unavailable.") }
    var error: Unmanaged<CFError>?
    guard let signature = SecKeyCreateSignature(privateKey, .rsaSignatureMessagePKCS1v15SHA256,
                                               Data(body.utf8) as CFData, &error) as Data? else {
        try fail("The update feed could not be signed.")
    }
    let wire = Data((body + "signature=" + signature.base64EncodedString() + "\n").utf8)
    guard wire.count <= 4096, try metadata(output) == nil else {
        try fail("The output already exists or exceeds the app's bounded feed contract.")
    }
    try wire.write(to: URL(fileURLWithPath: output), options: .atomic)
    guard chmod(output, 0o600) == 0 else { try fail("Could not protect the signed feed output.") }
    let feedDigest = SHA256.hash(data: wire).map { String(format: "%02x", $0) }.joined()
    print("Signed feed: \(output)\nAPK SHA-256: \(digest)\nFeed SHA-256: \(feedDigest)")
}

func signTest(home: String, path: String, password: String, input: String, output: String) throws {
    guard input.contains("androidTest"), output.hasPrefix("/private/tmp/"),
          let source = try metadata(input), source.st_mode & S_IFMT == S_IFREG,
          source.st_uid == getuid(), source.st_size > 0, source.st_size < 8 * 1024 * 1024,
          try metadata(output) == nil else {
        try fail("Test signing accepts only a local androidTest APK and a new private temporary output.")
    }
    let executable = home + "/Library/Android/sdk/build-tools/36.0.0/apksigner"
    let environment = childEnvironment(home: home, password: password)
    _ = try run(executable, arguments: ["sign", "--ks", path, "--ks-key-alias", keyAlias,
                                    "--ks-pass", "env:" + passwordVariable,
                                    "--key-pass", "env:" + passwordVariable,
                                    "--out", output, input], environment: environment)
    let verification = try run(executable, arguments: ["verify", "--print-certs", output],
                               environment: environment, publicOutput: true)
    guard let result = String(data: verification, encoding: .utf8),
          result.contains("Signer #1 certificate SHA-256 digest: 9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2") else {
        try fail("The isolated test APK did not verify with the release certificate.")
    }
    print("Signed isolated instrumentation APK: \(output)")
}

do {
    guard CommandLine.arguments.count >= 2,
          (CommandLine.arguments.count == 2 && ["create", "info", "build", "build-play"].contains(CommandLine.arguments[1])) ||
          (CommandLine.arguments.count == 5 && CommandLine.arguments[1] == "sign-feed") ||
          (CommandLine.arguments.count == 4 && CommandLine.arguments[1] == "sign-test") else {
        try fail("Usage: swift script/android_release_signing.swift create | info | build | build-play | sign-feed APK HTTPS_RELEASE_URL OUTPUT | sign-test ANDROID_TEST_APK TEMP_OUTPUT. No password arguments are accepted.")
    }
    umask(0o077)
    let command = CommandLine.arguments[1]
    let home = try ownerHome()
    guard fileManager.isExecutableFile(atPath: keytool) else { try fail("Android Studio's bundled keytool is unavailable.") }
    let directory = try signingDirectory(home: home, create: command == "create")
    let path = directory + "/amplifai-android-release.p12"
    let keychain = try loginKeychain(home: home)
    if command == "create" {
        try createIdentity(home: home, directory: directory, path: path, keychain: keychain)
    } else {
        try requirePrivatePath(path, directory: false)
        let password = try readPassword(keychain, allowPrompt: command != "build-play")
        try printPublicIdentity(path: path, environment: childEnvironment(home: home, password: password))
        if command == "build" { try buildRelease(home: home, path: path, password: password) }
        if command == "build-play" { try buildRelease(home: home, path: path, password: password, play: true) }
        if command == "sign-feed" {
            try signFeed(path: path, password: password, apk: CommandLine.arguments[2],
                         releaseURL: CommandLine.arguments[3], output: CommandLine.arguments[4],
                         environment: childEnvironment(home: home, password: password))
        }
        if command == "sign-test" {
            try signTest(home: home, path: path, password: password,
                         input: CommandLine.arguments[2], output: CommandLine.arguments[3])
        }
    }
} catch {
    fputs("Android signing: \(error)\n", stderr)
    exit(1)
}
