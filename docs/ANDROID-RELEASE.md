# Protected Android release

The publisher remains `JmooreOsirs/amplifai-phone-companion`. Android source comes from a reviewed normal merge into `release/native`; workflow/control source comes from reviewed `main`. Jeff (`JmooreOsirs`) and Brian (`bac1876`) may dispatch. A rerun by a different actor is rejected.

## Environment configuration

Create **amplifai-android-signing** with custom deployment branch policies allowing exactly the branch `main`. No tag pattern, wildcard, or per-run reviewer rule is needed. Preserve current protected-main/release-native PR/check/admin policies and CODEOWNERS. Reuse **amplifai-release-publication** with the same exact main-only policy.

The signing environment needs these exact secret names:

- `ANDROID_RELEASE_PKCS12_BASE64`: the existing official PKCS12 bytes, base64 encoded, transferred privately.
- `ANDROID_RELEASE_PASSWORD`: the existing store/key password. No replacement key is generated.
- `RELEASE_POLICY_AUDIT_TOKEN`: the existing read-only repository Administration audit credential, used only to verify protection configuration.

The publication environment needs only its existing `RELEASE_POLICY_AUDIT_TOKEN`. It receives no Android signing material. Build jobs receive no environment secrets. All jobs use minimal job-scoped `GITHUB_TOKEN` permissions; only publication can write repository contents.

The fixed alias is `amplifai-android-release`. The RSA-3072 certificate must remain SHA-256 `9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2`. Private values are never printed or stored in Git, artifacts, issue bodies, or workflow input fields.

## Release sequence

1. Normally merge the native candidate PR after required checks. Dispatch **Android secret-free build** from `main`, supplying the exact native merge SHA.
2. Read back the immutable build artifact and source archive, verify tests/lint and APK identity, then normally merge a release entry in `release_control/android_policy.json`. The entry pins the exact source, version/build, build control SHA/run/artifact/digest, unsigned APK, instrumentation APK, source archive, and previous production feed bytes/hash. Empty policy has no authorized release.
3. Dispatch **Protected Android release** from `main` in `sign` mode with the admitted version. Only trusted main control code reads the key. Candidate build scripts, Gradle and APK code are not executed in that job. The signed APK, signed instrumentation, source, feed and receipt are retained as one immutable Actions artifact.
4. Qualify those exact signed bytes with synthetic data, including supported Android/IME/selection, authority expiry/revocation, ACK and cleanup checks. Record exact artifact/APK and runtime-evidence digests. Normally merge that publication admission into the same release entry.
5. Dispatch `publish` for that version. The publisher independently validates the successful signer run and artifact, APK identity/certificate and feed signature, both previous production feed bodies, version order and absence of the proposed tag/release. It publishes exactly the tested APK, corresponding source, signed feed, qualification and checksums. It downloads draft bytes before publication and public bytes afterwards. Existing releases are never overwritten.
6. Give the verified publication receipt to the existing website owner for admission and feed promotion. Physical account/device acceptance remains separately evidenced.

All third-party Actions are pinned. The build uses the committed Gradle wrapper and exact Android SDK package names; its Gradle subprocess receives a clean environment without GitHub or signing credentials. Signer temporary PKCS12 files are owner-only and removed in both process cleanup and an always-run workflow step. Hosted runners are disposable.

An interrupted publication can leave an exact tag or draft. The next run intentionally refuses to overwrite it. Inspect its identity and saved receipts before choosing a recovery action. Restoring prior website/feed pointers does not downgrade an installed app; an installed-app correction requires a higher build.
