# Windows iPhone backup unlock recovery — RC30 candidate

## Outcome and ownership

Jeff requested an integrated iPhone-to-Windows app repair instead of requiring Lori to run a separate diagnostic. The active source is the existing `amplifai-phone-oct22-recovery` worktree, branch `codex/windows-backup-unlock-nov13`, based on protected `release/native` commit `37b0cdf`. The coordinator owns integration/release; the assigned worker owns collector and Windows recovery code and tests. No competing implementation lane is authorized.

Current status: package, frozen recovery, source/frozen UI, and runner upgrade passed; existing-key distribution signing complete; **signed update handoff and publication pending**. Live rollback remains Windows `2026.11.06-rc29` / `26110629` / MSI `1.0.29`. Candidate identity is `2026.11.13-rc30` / `26111330` / MSI `1.0.30`.

Model routing: continue the active native implementation setting; no model-family, paid-speed or spending escalation requested. Coordinator and workers remain separate.

## Verified defect and bounded repair

- Current RC29 terminates a fully received encrypted backup when keybag unlock fails. An incorrect password and a damaged class key both produce `backup_password`; that code alone cannot establish the cause.
- Synthetic real-crypto evidence reproduces failure with the correct password when another class key is corrupt. Password transport preserves spaces and Unicode without trimming.
- Windows lacks specific unlock-failure guidance and shows generic device-permissions advice.
- Add at most three explicit local password attempts within the same live completed backup, Windows opt-in only. Every retry requires new user input; no automatic repeated guessing, stored password, repeated USB acquisition or reopening a retained private backup.
- Catch only the dedicated error at `Keybag.from_manifest`. Manifest decryption, selected-payload crypto/length failures, source validation, account approval, cancellation and cleanup remain enforced. Never skip a class key or accept a database solely because it opens.
- Mac's default single-attempt behavior remains unchanged. No website Save or customer data changes belong in this repair.

## Finite gates

1. Red/green tests: wrong then correct within one acquisition; three-attempt limit; corrupt keybag remains closed; cancellation/EOF/authority; no records/password leakage; nonretryable integrity errors; default compatibility.
2. Windows state and UI tests: prompt and attempt status, same local backup, helper wire opt-in, stop/revocation, no terminal failure on a retry prompt.
3. Exact Windows source and frozen-package gates, guided UI, RC29-to-RC30 upgrade with existing session data preserved.
4. Only after qualified bytes exist: corresponding source, existing-key distribution signing, signed update-handoff gate, protected merge, coordinated website admission/feed/download update, public byte verification.

The signed-handoff workflow now pins the exact RC30 build artifact and public signed manifest, using published RC29 as its baseline. Never count a historical handoff result as RC30 qualification.

## Exact qualified source and package

- Product source: `e94115ac90af0e2fda580d13b42a907f6be33761`; native PR #53. Later release-control changes do not alter the frozen product.
- Windows build run `43475393505`: passed; 571 tests with six expected skips; frozen protocol/recovery and RC29-to-RC30 runner upgrade passed. Artifact `14064166027`, SHA-256 `c6e33cd75cec3d96fc87922f0f26ec313f5f48b030c398aae8781334b86e2e8f`.
- Guided run `43475438285`: passed, 244 source and 244 frozen scenario renderings with matching geometry/styles. Artifact `14065070156`, SHA-256 `8dc5d6747acfb1584f4dff9fbfa5a64e732b12657ec3672823c5a43c4daa29d5`. Coordinator visually reviewed retry, final attempt and exhausted states at normal/compact high-DPI sizes.
- MSI: 43,785,078 bytes, SHA-256 `1840406ccedc03c9d4920eb0536f901a330ef8a13a8c69de93303ad4472d9eeb`.
- Corresponding source: 423,876,014 bytes, SHA-256 `bbe6d98caf78e27a916ea646460a3c7dac3859bf0a89fa3e8e444eb50364eeb6`. All 1,267 archive members verified; retained 174 dependency source archives and 173 native references.
- Signed manifest SHA-256 `aacb8271abfadd0e8394cc7864ad6ba5da68d1111adc06c4d9b7cbf834a197ab`; detached signature SHA-256 `338c7be95030aef74a110e12d58bf4398f566b2f2adff988864d8e631ab3ae25`. Existing Ed25519 public key independently verified; no private key exported. Windows publisher trust remains unsigned Test.
- Next milestone: exact signed normal-app upgrade handoff, protected merge/public body readback, then coordinated website download/admission/feed activation. Evidence and helper receipts are retained in the selected checkout's ignored `build/rc30-release/` directory.

## Evidence and limits

- Prior RC29 focused boundary suite: 11 tests passed. Prior Windows decryption UI callbacks: 7 tests passed. These are baseline results, not candidate acceptance.
- Candidate collector regression suites: 136 passed, including real cryptography, corrupt first/later class keys, zero record writes before unlock, same-acquisition recovery and cleanup.
- Package and signed-manifest boundary tests: 34 passed. Release-control tests: 42 passed. Changed offline reader compatibility suite: 8 passed with an isolated cached APSW import (not added to the release).
- Windows source suite: 353 tests, 15 expected platform skips, no failures. Source IPC qualification: 36 real-crypto cases. Guided acceptance prepared for 61 scenarios across four viewports; actual Windows source/frozen rendering remains required.
- Synchronous authority recheck at password Submit blocks expired, refused, cancelled or paused native approval.
- Exact commit and Windows Actions evidence are recorded above; publication and Lori's device acceptance remain separate.
- Local cache runtime matches locked pyiosbackup 0.2.4, cryptography 50.0.1, pymobiledevice3 10.4.0 and Pillow 12.3.0. A separate binary-only lock install stopped before installation because hexdump 3.3 has no wheel; no dependency/lock fallback was used.
- Lori's exact cause remains unresolved. Synthetic recovery and a published build do not establish physical-device acceptance. The standalone diagnostic is not being executed, signed or distributed by this task.
- No private backup capture, passwords, customer account resets, encryption-setting changes, automatic Save, production importer changes or unrelated releases.
