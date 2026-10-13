# RC24 Intel source reconciliation

The Intel implementation comes from [Osirs-io PR #1](https://github.com/Osirs-io/amplifai-phone-companion/pull/1), base `81ecfb5aaf14571a7879d8bd5964cea4c2096a2c`, focused commits `d42fc39faa380e31592a1ae6fab28e19060b2a4a` and `9719cc230d7d57e5ea42a73c43222e3a83e40434`. The base is the older published RC23 source.

For this candidate, the authored `source/` tree was replaced with the SHA-256 verified [published RC24 source ZIP](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.13-rc24-release), then the two focused Intel commits were cherry-picked in order. The Swift `ContentView.swift` changes merged with RC24's newer Finder guide without a conflict. The RC24 Python collector changes, three new provenance tests, and the untouched Python and Swift dependency locks were retained. `INTEL-RC24-BASELINE.json` lists the 108 source-file hashes before the Intel patch; the builder checks all 103 unchanged baseline files before packaging.

The RC23 native CI run and its unsigned candidate are historical evidence. Build this RC24 integration on the standard native Intel runner. The workflow retains its output in an unpublished draft in `JmooreOsirs/amplifai-phone-companion`; it does not alter the Apple Silicon RC24 release, website, or updater.

Release qualification still requires exact RC24 Intel build/architecture and source receipts, Developer ID signing, notarization, staples, Gatekeeper, public-byte verification after approval, and owner-controlled Intel Mac/iPhone collection through account Save, report, and return login before calling it user-ready.
