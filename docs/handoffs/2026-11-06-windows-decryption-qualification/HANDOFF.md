# Windows encrypted-backup decryption qualification candidate

Status: local source candidate. No Windows replacement MSI has been built or
qualified, and no release, feed, admission, website, account or customer PC was
changed. This does not establish the cause of the reported RC28 failure.

## Source and preservation

Canonical native integration is `JmooreOsirs/amplifai-phone-companion`, protected
`release/native`, base `ad38665c0a274b5c09b0aae1bd339a68c5f127f9`. The isolated
local branch is `codex/rc28-decryption-qualification-nov06`.

Compared with released RC28 source `4caa24fdeb9a88d440eb9dc0e7b3b899e588beae`,
that base has only three later documentation/planning changes. Those changes are
preserved. The engine's current `main` retains PR75; its historical collector and
the projection's old `canonicalNativeHead` are not the current Windows authority.
Existing workspace checkouts, untracked CRM work and other local worktrees were
not edited. The candidate lives in a separate local checkout.

Reviewed input ZIP SHA256:
`8de530e9bcc1bb6cdd6ba01767e0da5902eb45944bf7736c19f6f859420d5a59`.
It was materialized from Library and its bytes verified before integration.

## Behavior

- Per-file key parse/class/unwrap/cipher failures become `selected_payload_crypto`
  after the keybag has been unlocked. Storage, cancellation and unrelated runtime
  exceptions retain their existing behavior.
- Manifest decryption/integrity failure remains fatal `backup_control_invalid`.
  Required contacts, including listed sidecars, remain fail-closed.
- Damaged optional Calls/Messages databases or WAL/SHM files become explicitly
  unavailable. Other valid sources can reach local review; unavailable is never
  interpreted as measured zero or complete historical coverage.
- Genuine keybag/password unlock failures remain fatal `backup_password`, now
  reporting the `processing` stage. Wrong password and damaged keybag still cannot
  be distinguished by this code.
- The contact action dock now visibly says `Calls unavailable`,
  `Messages unavailable`, or both before Continue, including compact layout.
  Previously the honest source model could be hidden behind Details while the
  dock showed generic Continue text. Review and account Save remain separate.
- Password bytes are neither normalized nor logged. Unicode, combining strings,
  spaces and the existing 512-character limit retain their exact values.

The proposed local candidate has distinct Windows identity
`2026.11.06-rc29` / build `26110629` / MSI `1.0.29`. Its feed URL, public key,
bundle ID, UpgradeCode and channel are preserved. No public identity is reserved
or activated by these local edits.

## Local evidence (Mac, CPython 3.12.14)

- Focused repair suite: **143/143 passed**.
- Final workflow source aggregate: **546 run; 535 passed, 11 platform skips**.
  It includes the existing selected-file, native-flow, session, updater, MSI and
  bridge checks plus the new decryption, visible-coverage and fixture checks.
- Release controls: **42/42 passed** with warnings treated as errors.
- New qualification harness: **31 source subprocess/OS-pipe executions**, including
  real AES/keybag/Manifest/SQLite parsing, required failures, optional database and
  sidecar failures, exact password transmission, review, cleanup and fresh
  wrong-password-to-valid retry. Five representative unmodified RC28 negative
  controls fail these assertions. These are source executions, not frozen proof.
- New visible-coverage test first exposed seven failing optional-source subcases.
  The final UI/model focus passed **151/151**. Actual Darwin Tk source geometry
  passed **12 cases**: Calls, Messages and both unavailable across four viewport/
  scaling combinations. No customer window, phone or account was used.
- Independent review found no material blocker; independent final harness/UI
  **8/8**, encrypted-size compatibility **40/40**, and expanded focus
  **175 passed, one Windows-only skip**.
- All 16 changed Python files compile. Ruff0.14.4 E/F checks, excluding existing
  long-line style E501, have the same eight pre-existing diagnostics as untouched
  base and **zero new diagnostics**. New repair/fixture files pass directly.
  Actionlint1.7.7 validates the changed workflow; whitespace checks pass.
  No separate Python static-type gate is configured; no TypeScript was changed.

A broader collector discovery was also attempted:523 run,484 passed,28 skipped,
11 import errors from unavailable `apsw` or `probes` dependencies in unchanged
offline/probe suites. No clean whole-repository discovery is claimed. The
existing Windows qualification aggregate above is green.

Reproduce the final aggregate using the module list in the workflow's
`Qualify diagnostics retention, Windows DACLs, session gates and update verifier`
step with `PYTHONPATH=source/collector:source/collector/tests:source/collector/windows:source/collector/windows/tests:source/script`.
Local loopback tests require permission to bind localhost. The Mac runtime's core
crypto packages match the repair environment, but this is not the complete
hash-locked Windows runtime or Windows DACL qualification.

## Pending Windows qualification

No local Windows VM or connected Windows desktop was established. Use the
existing workflow `.github/workflows/windows-rc18-updates.yml`, registered as
workflow ID384813334. It is active; current default `main` no longer carries that
file, so dispatch eligibility on the pushed candidate ref must be confirmed by
the service when the authorized dispatch is attempted.

The precise pending remote operations are:

```sh
git push origin HEAD:refs/heads/codex/rc28-decryption-qualification-nov06
gh workflow run 384813334 --repo JmooreOsirs/amplifai-phone-companion --ref codex/rc28-decryption-qualification-nov06 -f phase=build
gh workflow run 384813334 --repo JmooreOsirs/amplifai-phone-companion --ref codex/rc28-decryption-qualification-nov06 -f phase=guided-ui
```

These operations remain pending authorization; none was attempted or denied.
Do not merge into main merely to repair a dispatch failure without approval.
The new branch does not match the old automatic draft-release branch triggers.
Use only `build` and `guided-ui`: `handoff` intentionally remains pinned to RC27.

The workflow uses Windows2025, exact CPython3.12.10 x64 and unchanged lock SHA256
`aa8ee933827681ebda483c2a889dc6156f7d9de906fe21d90f055fd06939c95d`. It
builds the ordinary helper/wrapper and a distinct MSI. The new, non-shipped
`frozen_decryption_fixture.py` compares source code with the ordinary helper PYZ
and loaded frozen fixture code, including all three repaired modules. It replaces
physical acquisition only and runs real decryption with fictional backup data.
Its executable stays outside the package/MSI. Existing Unicode pipe and encrypted
SQLite-worker probes remain separate checks. Strict source/frozen guided rendering
adds Calls, Messages and both-unavailable cases.

The upgrade gate now pins released RC28 MSI size43785078 and SHA256
`d78576276c9031fbfd39b9c980ea23c66b471609a863e6f85277d9eee1041768`, and
checks RC28-to-RC29 replacement, installed bytes, uninstall and an unchanged
fixture session sentinel on the disposable runner. That does not establish
consumer SmartScreen/USB behavior or migrate an active in-memory review.

## Release and later retry boundary

After successful Windows runs, inspect exact frozen/helper/UI/installer receipts
and artifact hashes. Matching corresponding source, release signing/distribution,
updater handoff, installer publication, download/feed/admission changes and any
website deployment require separately approved scope. The approved Windows test
must retain artifacts only, without creating even a draft release.

Do not describe this as ready for a customer until a matching qualified replacement
installer exists. A later retry should be a fresh approved collection with the
existing backup encryption password entered only in the local app. No password
reset, disabled encryption, backup deletion, restore, private backup upload or
customer outreach is part of this work. A remaining fatal keybag/manifest error
still needs further diagnosis; partial data must remain visibly partial.
