# Mac encrypted-backup unlock recovery

## Scope and current state

Jeff requested investigation and repair of an iPhone-to-M4-Mac failure in RC35, plus review of a proposed standalone key-check diagnostic. Source work uses the existing `amplifai-phone-oct22-recovery` checkout, branch `codex/macos-backup-unlock-nov13`, based on protected native merge `b745020d6f5da899d738d90c18063f58c2d0e600`. The existing Windows RC30 release is complete and remains separate. Implementation setting: standing approved GPT-6.1 Sol / Medium.

RC35 build `26102235` corresponds to tag `macos-2026.10.22-rc35-release` at `dfc8075`. Its `backup_password` category includes encryption-key unwrap failures; the UI incorrectly states that the password was rejected. The reported failure was in local processing after acquisition. Transfer/retention counters do not prove that temporary files remain after cleanup, nor that account records were saved. The customer's exact cause is not established.

## Repair and finite gates

- Opt the Mac connection into the shared helper's existing maximum of three explicit password submissions during the same live acquisition. No automatic guessing, password normalization/persistence, second USB backup, or reopening an ended capture.
- Validate prompt fields and attempt sequence, and recheck the same live collection authority before submitting a password. Preserve expiry, revocation, cancellation and cleanup. Do not mask a pipe-write failure with a later processing state.
- Use existing Mac UI primitives and factual copy: an unlock failure can mean the password or backup encryption keys could not be used. Do not skip class keys, weaken manifest/payload validation, or promise recovery from damaged keys.
- Qualify changed Mac decoding/model/authority behavior, synthetic encrypted-backup recovery, full native compilation and rendered prompt states. A packaged helper check, corresponding source, signing/notarization and downloaded-app acceptance are separate release gates.

## Standalone diagnostic and release dependencies

The proposed recipe `mac-diagnostic-release-review-v1/START-HERE-CURRENT.md` and sibling `mac-portable-key-check-build-v1` were reported to exist only on Brian's Mac. The equivalent supplied path is absent here; scoped Downloads and current repository checks did not locate the Mac materials. Jeff confirmed that the screenshots, support code and pasted description are all the materials he has. No package test claims have been independently verified; the integrated repair proceeds independently.

The proposed diagnostic assumes an existing Finder backup. Neither the screenshots nor the companion's temporary transfer counters establish that such a backup exists. Do not ask the customer to repeat a large backup merely to make an unreviewed diagnostic runnable.

Before distribution, review the exact source, dependency/license closure, bounded control-metadata-only reads, local password handling, absence of customer content and network uploads, and complete in-app Legal files. Rebuild a distinct candidate rather than modifying an already signed artifact. The diagnostic must not enter the production importer/update feed or be described as an importer fix.

On November 13, the existing Developer ID Application identity was valid. A read-only `notarytool history` request using the existing `AMPLIFai-notary` profile returned HTTP 403: a required agreement is missing or expired. The Apple Account Holder must resolve this before notarization. No key or password was exported, and no agreement was accepted by the agent.

Jeff said he will review the Apple agreement; acceptance and restored service access are not yet confirmed.

## Source verification completed

- A new regression failed on the original Mac behavior: the app wrote a password before a held preparation-status check returned. The targeted check passed after fencing submission on the same live approval.
- Twelve real-cryptography synthetic recovery tests passed on this Mac using the existing pinned interpreter. They cover same-acquisition recovery, the three-attempt bound, corrupt first/later keys, distinct manifest/source failures, cancellation, cleanup and exact Unicode/space handling. Evidence: `build/mac-unlock-review/crypto-recovery.log`.
- The broader flow fixture now submits its password before presenting capture, matching the actual helper sequence. It previously jumped from a password prompt to capture without submission.
- Full Mac model checks passed, including 311,149 synthetic records across 3,114 pages, two real 30-second authorization refresh cycles, the 65-second watchdog, receipt gates and legacy terminal callbacks. Evidence: `build/mac-unlock-review/model-full.log`.
- Mac flow checks passed, including strict native setup, explicit consent, cancellation, operation fences, legacy isolation and the synthetic selected-Save receipt. Evidence: `build/mac-unlock-review/flow.log`.
- Optimized arm64 Swift compilation passed after the final production edit. Evidence: `build/mac-unlock-review/swift-release-build.log`. This compiles the wrapper; it does not qualify a frozen portable helper or signed installer.
- The production-view offscreen renderer completed 24 images: first attempt, second attempt, final attempt and exhausted state at 375×844, 768×900, 1440×1000, 1180×820, 820×640 and 1180×680. Eight representative images were visually inspected across five sizes; prompt/error copy and controls remained legible, using the existing native styles. Compact exhausted screens scroll to the next section. The yellow test watermark is fixture-only. The renderer receipt confirms no app activation, helper start, customer workspace read or desktop capture.
- New tests cover fresh-authority fencing, replay/skipped prompts, exact Unicode and spaces, one submission per click, closed-stdin failure and terminal cleanup. Strict manifest, class-key and payload checks remain unchanged.
- `git diff --check` passed. `VERIFICATION.json` records the reviewed source hashes and local evidence hashes. These results qualify this source repair for review, not for public distribution or customer-device acceptance.

## Remaining release work

Prepare and verify the complete portable Mac candidate and corresponding source under the existing release recipe. Resolve Apple account access, then sign/notarize/staple and verify the downloaded package opens normally. Coordinate exact website admission and feed pins before publication. Preserve the current Mac release for rollback. No new Mac release version has been assigned in this source patch.

The customer's password versus encryption-key cause is still unproven. The repair offers bounded same-session recovery and truthful failure handling; it does not reconstruct damaged keys or restore the already-ended RC35 capture. The unavailable standalone diagnostic has not been reviewed or approved for distribution.

No private backup/device access, customer reset, account Save, production feed change, diagnostic execution/distribution or Mac publication has occurred. The ended RC35 capture cannot be recovered by this source change alone.
