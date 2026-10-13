# Intel Mac support — RC23-based implementation

## What this changes

This branch imports the authored source from Jeff's published `macos-2026.10.12-rc23-release`, then adds a separate Intel build. The upstream `main` branch was older than RC23 when this work began. `INTEL-RC23-BASELINE.json` records the archive URL, verified SHA-256 and every imported source hash. Do not replace the current collector with the older upstream main tree.

The existing app is not fundamentally dependent on Apple silicon. The concrete packaging blockers were host-native Swift/Python builds, an ARM-oriented dependency lock, a cryptography release without an Intel wheel, ARM-only test targets, and one updater feed serving the ARM release.

The new path:

1. Uses native `macos-15-intel`, CPython 3.12.15 and an explicit x86_64 Swift/PyInstaller target.
2. Preserves all 102 locked Python package versions. Builds cryptography 50.0.1 from the exact existing source using pinned Rust 1.98.0 and a private static OpenSSL 3.5.9. Hash-checks every downloaded input, vendors locked Rust crates and builds without Homebrew.
3. Checks the architecture, minimum-OS load commands, internal links and hashes of every bundled Mach-O, not just the app executable.
4. Disables both automatic and manual Intel updater actions until an Intel-compatible signed feed exists. ARM updater behavior stays unchanged.
5. Produces a separate ad-hoc signed candidate and truthful receipts. It neither installs over another app nor changes an account database, website, original ARM release, or phone.

## Build

Use `.github/workflows/macos-intel.yml` in the fork. For the focused import and manual build/output instructions in Jeff's repository, see `INTEL-MERGE-INSTRUCTIONS.md`. It uses a standard GitHub Intel runner in a public repository, not a paid larger runner. It never accesses signing secrets. Local equivalent on a native Intel Mac:

```sh
python3.12 source/script/prepare_intel_dependencies.py --output-dir /new/path/intel-deps
python3.12 -m venv /new/path/intel-venv
/new/path/intel-venv/bin/python -m pip install --no-index \
  --find-links /new/path/intel-deps/wheelhouse --require-hashes \
  -r /new/path/intel-deps/runtime-requirements.lock
/new/path/intel-venv/bin/python source/script/collect_intel_python_sources.py \
  --runtime-archive /path/to/pinned-intel-python.tar.gz \
  --output-dir /new/path/intel-python-sources \
  --reuse-source-dir /new/path/intel-deps/source-inputs
/new/path/intel-venv/bin/python source/script/build_macos_intel_candidate.py \
  --output /new/path/intel-build \
  --dependency-receipt /new/path/intel-deps/receipt.json \
  --python-sources /new/path/intel-python-sources
```

Supply Rust/Cargo 1.98.0 before preparing dependencies. The workflow pins the Intel Python download and checksum. Build outputs must be new directories, preserving prior candidates. The builder never starts phone collection.

## What Jeff needs to finish for ordinary users

1. Review the focused changes after the RC23 import commit. If the canonical collector has advanced, apply these build/updater changes individually to its current source and rerun qualification.
2. Sign the exact Intel app with the existing authorized Developer ID identity, notarize/staple it, package/sign/notarize/staple the Intel DMG, then verify a normal downloaded installation through Gatekeeper. Signing can happen on Jeff's existing Mac; a separate Intel Apple membership or private-key transfer is not required.
3. Keep the ARM updater feed unavailable to Intel until it is explicitly architecture-aware or a separate signed Intel feed is qualified. Do not remove the compiled updater guard merely to make the button appear enabled.
4. Add a separate **Mac — Intel** release record on the canonical website, with the exact Intel artifact URL, SHA-256, version and supported macOS floor. Enable its pairing/account route only with the compatible artifact. Preserve Apple-silicon and Windows entries. Check the canonical equivalents of `src/lib/release/phone-companion.ts`, `src/components/phone-setup-state.ts`, and `src/components/phone-connection-setup.tsx`: the existing host allowlists and restored-step normalization only admit `apple-silicon`, not `intel-mac`. Changing only the download link leaves setup blocked. The website/backend source is outside this public companion repository and was not changed here.
5. On a physical Intel Mac, verify normal launch, encrypted iPhone collection, known incoming/outgoing call review, selected-contact transfer to the displayed account, Save acknowledgements, Finalize, report and sign-out/sign-in persistence. Test interrupted-save/report retry using retained results rather than requiring a new phone collection.
6. Test Intel macOS 14 specifically before advertising 14 as verified. Static minimum-OS checks and a build on macOS 15 do not prove execution on 14. Macs that cannot run 14 require a separate UI/back-deployment review; this patch does not claim support for every Intel Mac ever sold.

## Research and support lifetime

The macOS 14 floor is primarily the current interface/deployment target, not an identified requirement of the iPhone collection protocol. Read-only compiler probes found a focused route for macOS 13: replace the ten two-argument SwiftUI `onChange` uses in `ContentView.swift` and `CollectionStatusPanel.swift` with compatible one-value handlers (the old value is unused), then rebuild all native inputs and qualify on 13. macOS 12 additionally requires alternatives for `ViewThatFits` and `defaultSize`. Those backports are not included or qualified in this macOS 14 candidate; do not simply lower `LSMinimumSystemVersion` or promise older-OS support.

- Apple still documents Intel targets. Xcode 27's development host requires Apple silicon, but its SDK supports Universal apps targeting older macOS versions: https://developer.apple.com/documentation/xcode-release-notes/xcode-27-release-notes
- All executable components must include the target architecture: https://developer.apple.com/documentation/apple-silicon/building-a-universal-macos-binary
- Current native Intel hosted runners: https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job
- GitHub's published Intel runner retirement horizon is fall 2027; plan a controlled Intel build host or a qualified universal2/Rosetta build path before then: https://github.blog/changelog/2025-09-19-github-actions-macos-13-runner-image-is-closing-down/
- Public repositories using standard runners have free Actions execution: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Homebrew's Intel bottle deprecation does not prohibit building an Intel app. This path avoids that dependency: https://brew.sh/2026/09/13/homebrew-7.0.0/
- PyInstaller supports x86_64 with matching native libraries; do not combine two finished frozen helpers with lipo: https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support
- Build/oldest-OS qualification guidance: https://pyinstaller.org/en/stable/usage.html#making-macos-apps-forward-compatible

## Evidence boundaries

The current build/test results belong in `INTEL-VERIFICATION.md` and machine-readable CI receipts. A successful source test, static audit, Rosetta execution, native Intel CI execution, physical iPhone test, and hosted report readback are different evidence. Neither this document nor a green build claims signing/notarization or real-phone/account completion.
