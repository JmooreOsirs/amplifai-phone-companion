# Windows encrypted-backup decryption qualification candidate

Status: unreleased source candidate under Windows qualification. The first
revision built and passed frozen decryption/MSI upgrade tests, but the guided UI
gate found a compact-layout regression. This revision corrects it and requires
matching Windows requalification. No release, feed, admission, website, account
or customer PC was changed. This does not establish the reported RC28 cause.

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
- Final workflow source aggregate after compact correction: **548 run;
  537 passed, 11 platform skips**.
  It includes the existing selected-file, native-flow, session, updater, MSI and
  bridge checks plus the new decryption, visible-coverage and fixture checks.
- Release controls: **42/42 passed** with warnings treated as errors.
- New qualification harness: **31 source subprocess/OS-pipe executions**, including
  real AES/keybag/Manifest/SQLite parsing, required failures, optional database and
  sidecar failures, exact password transmission, review, cleanup and fresh
  wrong-password-to-valid retry. Five representative unmodified RC28 negative
  controls fail these assertions. These are source executions, not frozen proof.
- New visible-coverage test first exposed seven failing optional-source subcases.
  The expanded UI/model focus passed **153/153**. Actual Darwin Tk source geometry
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

## Windows execution and compact-layout correction

The explicitly authorized isolated push and artifact-only dispatches succeeded.
First revision `5c8f6a5faab13c8dcfee28431749c2e014ef540e`:

- Build run42299666945 passed:546 Windows source tests (540passed,6expected
  platform skips), both large Messages gates, ordinary helper/wrapper freeze,
  frozen Unicode/worker checks,31real-decryption frozen cases and RC28-to-RC29
  MSI upgrade/install/uninstall/session preservation. All12checked production
  modules matched source, ordinary packaged helper and frozen fixture.
- Its MSI is43785078bytes, SHA256
  `81be0d9516f500f3859a41256b473add8721f1e584b3181c51452af9565239f6`.
  This earlier artifact is not qualification of subsequent UI edits.
- Guided run42299693429 failed source geometry at `reviewed-contacts`,680x680,
  Tk scaling1.75. The unavailable prefix plus generic Continue status wrapped
  to two lines; combined with the large selection header it left73pixels for
  cards, below the existing three-line readability requirement. The three new
  crypto capture cases had passed but used a small selection count.

The correction shortens only generic compact continuation copy while keeping
explicit unavailable coverage. Recovery, binding and saving messages retain their
meaning. Three reviewed-crypto scenarios now use the existing1,205-selected
fixture, exercising the large header plus partial warning. Existing geometry and
readability requirements remain unchanged. The new revision must rerun both
Windows build and full source/frozen guided gates before a readiness conclusion.

## Windows requalification

No local Windows VM or connected Windows desktop was established. Use the
existing workflow `.github/workflows/windows-rc18-updates.yml`, registered as
workflow ID384813334. It is active; current default `main` no longer carries that
file, so dispatch eligibility on the pushed candidate ref must be confirmed by
the service when the authorized dispatch is attempted.

The requalification operations are:

```sh
git push origin HEAD:refs/heads/codex/rc28-decryption-qualification-nov06
gh workflow run 384813334 --repo JmooreOsirs/amplifai-phone-companion --ref codex/rc28-decryption-qualification-nov06 -f phase=build
gh workflow run 384813334 --repo JmooreOsirs/amplifai-phone-companion --ref codex/rc28-decryption-qualification-nov06 -f phase=guided-ui
```

The user authorized these isolated test actions; no operation was denied.
The earlier dispatches worked despite the default-branch file absence. Do not
merge into main merely to repair a later dispatch failure without approval.
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
