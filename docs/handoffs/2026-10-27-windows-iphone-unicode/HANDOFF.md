# Windows iPhone collection failure and UTF-8 protocol repair

Date: 2026-10-27. All incident times below are UTC.

This document is the engineering handoff for a repeated Windows iPhone collection
failure after backup reception. It records what was observed, what has been
reproduced, the repair, and the evidence still required before calling the repair
accepted on a physical phone. It contains no customer account identity, phone
contents, device identifier, password, token, or private recording link.

## Current status and scope

| Item | Verified status at this handoff |
|---|---|
| Public repository / branch | `JmooreOsirs/amplifai-phone-companion`, `codex/windows-iphone-reliability` |
| Review | [Draft PR #49](https://github.com/JmooreOsirs/amplifai-phone-companion/pull/49), open and unmerged; public head remains `b94e6f4` (RC26) |
| Local repair commit | `a6779fccaf921d294e3c29b7dd2c077e31848c50` — explicit UTF-8 protocol and RC27 qualification |
| RC27 identity | App `2026.10.27-rc27`, build `26102727`, MSI `1.0.27`, `AmplifaiPhone-rc27-unsigned.msi` |
| RC27 publication / Windows CI | Pending; no RC27 CI run, frozen Windows pass, installer artifact or installer checksum is established yet |
| Local checks | Aggregate: 510 run, 499 passed, 11 platform skips. Release controls: 42 passed. Final diagnostics/harness checks: 18 passed. Diff check passed |
| Physical acceptance | RC26 failed as described below. RC27 physical collection, selection, account Save and report reopening are unverified |
| Website admission | Live website was verified at backend commit `ac99dfccdd361875f56ea5aa23ee7f5e0cf2ba12`, admitting exact RC26. RC27 requires its own narrow compatibility change and deployment approval |

The RC27 candidate is a focused collection/protocol repair. It does not change
the public download or signed update feed, remove collection/destination approval,
publish a production release, or change the phone's security settings. Do not
overwrite RC26 or earlier immutable artifacts with RC27 bytes.

**Naming caution:** [`docs/RC27-NATIVE-INTEGRATION.md`](../../RC27-NATIVE-INTEGRATION.md)
describes an older integration lineage. Historical branches named `rc27` are not
the dated Windows `2026.10.27-rc27` candidate; their Windows pins were RC19.
Use the complete version/build/MSI tuple, source commit and artifact hash.

## Incident and correction history

| Sequence | Evidence and resulting change |
|---|---|
| Released RC25 | Windows `2026.10.26-rc25`, build `26102625`, MSI `1.0.25`. A physical run received the backup, requested its encryption password, entered local source reading, then failed before contact selection or confirmed account Save. A processed counter near 1.1 GiB did not identify a particular source or prove a size limit |
| RC25 diagnostic gap | The helper produced bounded error code/stage, but the Windows presentation discarded those fields. A subsequent automatic temporary-data inspection obscured the original failure. Raw stderr was intentionally not retained, so the original exception could not be recovered |
| `4cf973468c8c0b698e5896404fd5155bd82d534c` | Preserved unknown call direction rather than inventing incoming/missed direction and repaired a stale bridge test. This is adjacent work, not evidence that call direction caused the post-download failure |
| `9b91add2b04d63c060a74bf8f49ad34031ab08eb` | Retained safe first-failure code, helper stage, UTC time and bounded exit/device status in memory through automatic inspection. Later recovery failures remain separate. Added meaningful failure/cleanup regressions |
| `b94e6f4aa3b6f8cdcdd11cc9b2a03fb536f9e1a5` | Gave that diagnostic candidate a distinct RC26 identity and qualified package/source/UI custody and RC25-to-RC26 MSI replacement |
| RC26 physical run | At `2026-10-27T15:03:57Z`, the app retained `collection_failed`, helper stage `review`, operation `connect`, helper exit `1`. The first capture packet was not accepted by the GUI. Account Save was not confirmed |
| RC26 read-only diagnosis | A numeric-only call-accounting record had the same failure-second timestamp. This establishes that collection returned and call-accounting retention ran before the terminal failure; the UI's “coverage has not been read” copy did not prove that the parser never ran. Windows ANSI code page was 1252; a normal diagnostic shell had no `PYTHONUTF8` or `PYTHONIOENCODING` override |
| Synthetic reproduction | A first-page fictional Unicode contact under a strict CP1252 output pipe produced the exact `collection_failed` / `review` / exit `1` signature. ASCII contacts succeeded. The same Unicode data with UTF-8 succeeded, including with 250 disk-backed records |
| `a6779fccaf921d294e3c29b7dd2c077e31848c50` | Pins actual helper input/output to strict UTF-8, pins its parent launch environment/decoder, retains safe fallback failure classification and review substep, and adds source plus isolated frozen Windows qualification |

The failed application was preserved during diagnosis. No new backup was started
by the diagnostic work, and no phone files, original external data, account data
or production release were changed by this repair.
Owned capture cleanup had already run, with no marked abandoned session reported.
Keeping the app open preserves diagnostic facts; it does not establish that a
reusable backup still exists.

## What the evidence establishes — and does not

`run_connect` in [`agent.py`](../../../source/collector/amplifai_phone/agent.py)
sets `capture_stage = "review"` immediately after
`run_local_capture(collector(...))` returns. It then validates source diagnostics,
retains call accounting, constructs a first bounded contact page and writes the
initial `capture` event. The stage does **not** mean the customer reached the
contact-review UI. The RC26 timestamped accounting record narrows the observed
failure to the work after that retention boundary; no initial capture event was
accepted by the GUI.

The encoding defect is concrete and reproducible, and matches the observed
boundary. `_emit` serialized JSON with `ensure_ascii=False`. Before this repair,
the Windows parent set `encoding="utf-8"` on its own `Popen` pipe wrappers but
did not configure the child's standard streams. The frozen console helper uses
CPython 3.12.10. Redirected Windows Python streams normally use the ANSI locale,
which can reject emoji or characters outside its code page. See the versioned
[Python standard-stream documentation](https://docs.python.org/3.12/library/sys.html#sys.stdout).
Progress packets can therefore succeed for an entire backup before the first
contact-name packet raises a Unicode encoding exception.

The original exception type, traceback and exact exited-helper environment were
not retained. A shell's environment and the system code page do not recover the
exited process's exact configuration. No real contact was inspected to find a
triggering character. Therefore this handoff calls the Unicode defect a strong,
matching cause, **not a forensic proof of the discarded exception**. RC27 also
adds safe classification so a different remaining failure is distinguishable.

Do not infer a fixed 1 GiB processing limit from the displayed counter: processing
includes control/manifest work as well as selected databases. Do not conflate
100% reported backup transfer, completed local parsing, contact review, browser
receipt and durable account Save. Each has a separate boundary.

## Repair design and privacy contract

- [`helper_protocol.py`](../../../source/collector/amplifai_phone/helper_protocol.py)
  reconfigures the actual input and output text streams to UTF-8 with strict
  errors. Input accepts platform newlines; output emits LF-delimited JSON with
  write-through enabled. It neither takes ownership of nor closes the caller's
  byte buffers. Injected in-memory streams without `reconfigure` remain testable.
- [`agent_entry.py`](../../../source/collector/agent_entry.py) configures stdio
  before dispatching helper modes. Agent main/connect/admin entry points also
  enforce the protocol contract, so a direct source entry cannot silently depend
  on its launch locale.
- [`windows/helper_process.py`](../../../source/collector/windows/helper_process.py)
  sets child `PYTHONIOENCODING=utf-8:strict` and `PYTHONUTF8=1`, and uses an explicit
  strict UTF-8 parent encoder/decoder. It still removes inherited Python module
  path/home overrides and isolates the child frozen runtime. Environment settings
  supplement actual stream configuration; they do not replace it.
- Legitimate Unicode is preserved without transliteration, replacement characters
  or dropping contacts. Malformed UTF-8 is rejected through bounded protocol
  failure handling. The parent closes input cooperatively and drains private
  output rather than automatically terminating a helper that may own temporary
  data.
- Fallback `collection_failed` events gain only a fixed `exceptionClass` enum:
  `unicode_encode`, `unicode_decode`, `value`, `type`, `os`, `runtime`, `other`.
  Review failures gain a fixed `reviewStep`: `diagnostics`, `call_accounting`,
  `source_diagnostics`, `contact_page`, `capture_packet`, `commands`.
- [`windows/session.py`](../../../source/collector/windows/session.py) retains
  only those allowlisted values at their valid error boundary. A protocol decoder
  failure may retain only `unicode_decode`; other protocol classifications are
  dropped. The fields appear in diagnostic details, without expanding the primary
  failure summary. First collection failure remains intact through inspection;
  a new explicitly approved collection resets it.

Never add raw exception messages, tracebacks, decoder bytes, phone rows, contact
names/numbers, passwords, paths, device identifiers, tokens or entire helper
events to persistent diagnostics. Existing source/accounting histories contain
bounded enums and aggregate counts; they are not a general log of phone contents.

## Reproduction and regression evidence

The source and frozen harness use 250 fictional contacts in the real disk-backed
`RecordStore`; the first preview page contains 200 contacts. The Unicode fixture
includes an emoji and non-Western characters. The successful path also exercises
a fictional Unicode password command, contact query, selection review and
matching fictional call/message totals. No Apple service, real phone or account
is accessed. The password round trip proves protocol preservation, not real
keybag/password decryption.

| Harness case | Expected result |
|---|---|
| `legacy-ascii` | CP1252 with old stream configuration: capture delivered, exit 0 |
| `legacy-unicode` | Negative control disables only the UTF-8 configuration: no capture, `collection_failed`, stage `review`, class `unicode_encode`, step `capture_packet`, exit 1 |
| `fixed-ascii` | Actual UTF-8 configuration overrides initially CP1252 streams; capture/query/review succeed |
| `fixed-unicode` | Same success while preserving fictional Unicode input and contact output exactly |
| `invalid-utf8` | Safe bounded decode-failure classification; no raw bytes or password leakage |
| `private-error` | Safe fixed runtime classification; private sentinel absent from output/receipt |

The local source integration checks are in
[`test_helper_protocol.py`](../../../source/collector/tests/test_helper_protocol.py),
[`test_frozen_protocol_fixture.py`](../../../source/collector/windows/tests/test_frozen_protocol_fixture.py),
[`test_helper_boundary.py`](../../../source/collector/windows/tests/test_helper_boundary.py)
and [`test_failure_diagnostics.py`](../../../source/collector/windows/tests/test_failure_diagnostics.py).
They also cover strict launch settings, malformed frames, cooperative EOF/drain,
safe enum filtering and failure retention after cleanup.

### Source versus actual frozen Windows proof

[`frozen_protocol_fixture.py`](../../../source/collector/windows/tests/frozen_protocol_fixture.py)
is a **separate, non-shipped test launcher**. It replaces only the physical
collector and calls production entry/main/result-building code with real redirected
stdin/stdout. It does not add a fake-phone mode or fixture to the customer MSI.
Its production-module code hashes are checked against both exact source and the
normal packaged helper's archive, ignoring only relocated code filenames. Eight
modules include entry, agent, protocol configuration, capture runtime, record
store and source parsing/review types. A changed executable/code map fails custody
validation.

The ordinary, unmodified packaged helper is separately run in `runtime-check`
under `PYTHONIOENCODING=cp1252` / `PYTHONUTF8=0`, without contacting a device.
That proves packaged entry/import readiness; its runtime-check does **not** accept
fictional contact/password input. The isolated frozen launcher supplies the
Unicode contact/input proof with matching production code. Neither proves a
customer-installed executable's physical transfer or account Save.

As of this handoff, the local harness cases pass, but **the actual Windows frozen
run is pending**. Do not replace that distinction with “packaged Windows passed”
until the receipts from the following job have been inspected.

## Exact commands and qualification workflow

Run from the repository root with its existing locked dependencies. The package
job requires Windows x64 CPython **3.12.10** on `windows-2025`. Local source results
were obtained with the available macOS CPython 3.12 environment; platform skips
are not Windows passes.

```bash
export PYTHONPATH=source/collector:source/collector/tests:source/collector/windows:source/collector/windows/tests:source/script
python -m unittest test_helper_protocol test_capture_runtime test_encryption_preparation test_helper_boundary test_agent test_ios_backup.IPhoneInterfaceTest test_metadata test_native_setup test_setup_delivery test_native_run test_native_app test_native_bridge_boundary test_destination_bridge test_sqlite_encrypted_size_compat test_sqlite_messages_size_compat test_windows_source_diagnostics test_source_diagnostics test_source_diagnostic_flow test_updater test_update_handoff test_session test_failure_diagnostics test_gui_guards test_video_parity test_companion_status test_windows_candidate test_windows_msi test_backup_stream test_journey test_pump_cadence -v
python -m unittest test_failure_diagnostics test_frozen_protocol_fixture -v
PYTHONDONTWRITEBYTECODE=1 python -W error -m unittest discover -s release_control -p 'test_*.py' -v
git diff --check
```

The aggregate includes loopback HTTP tests with fictional data; a restricted
environment that denies binding `127.0.0.1` cannot qualify those tests. The final
local aggregate was run with loopback permitted and had no failures.

After approved publication of the exact commit, dispatch the active package job:

```bash
gh workflow run windows-rc18-updates.yml \
  --repo JmooreOsirs/amplifai-phone-companion \
  --ref codex/windows-iphone-reliability -f phase=build
```

Despite its historical filename, this is the active **Windows source and package
qualification** workflow. `phase=guided-ui` is the full source/frozen UI gate if
needed for a changed presentation. **Do not dispatch `phase=handoff` for RC27:**
that historical path remains pinned to an earlier release. Do not use its outputs
as RC27 evidence or publish them over another release.

The package job sets Windows `PYTHONPATH` with semicolons, freezes the normal
candidate, qualifies the encrypted worker, then invokes:

```powershell
$candidate = Join-Path $env:RUNNER_TEMP 'AmplifaiPhoneCandidate'
$helper = Join-Path $candidate 'wrapper-dist\AmplifaiPhone\resources\phone-helper\AmplifaiPhoneHelper.exe'
$output = Join-Path $env:RUNNER_TEMP 'AmplifaiProtocolQualification'
python -m unittest test_frozen_protocol_fixture -v
python source/collector/windows/tests/frozen_protocol_fixture.py --source source --helper $helper --output $output
```

The output must be new and runner-owned. The frozen builder is deliberately
restricted to the disposable Windows Actions environment, not a customer's PC.
Inspect `protocol-manifest.json`, `frozen-protocol-receipt.json`, `freeze.log`, the
candidate/MSI/upgrade receipts and unit logs. Require all six cases, code custody,
owned cleanup, no private diagnostic output and no physical-account claim.

The upgrade gate retrieves the exact previous RC26 artifact and qualifies
**released RC25 → RC26 → RC27** under the runner user. It verifies a session-data
sentinel across replacement and uninstall and checks installed files/protocol
registration. It does not run the installer on the customer's PC. UpgradeCode
remains `0C4BE78C-C5E1-4EA9-841B-4D8D93F45871`.

## Known RC26 evidence and immutable artifact identity

- [RC26 package run 40511748092](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40511748092): successful at exact `b94e6f4`; 494 Windows tests, 488 passed, 6 skipped; encrypted worker and RC25 → RC26 runner upgrade passed.
- [RC26 guided UI run 40511685930](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40511685930): successful; 204 matching source/frozen screenshot pairs, including diagnostic failure states. This is RC26 evidence, not an RC27 frozen Unicode result.
- [RC26 artifact 12738323362](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40511748092/artifacts/12738323362), name `windows-rc26-qualification`; retention expiry recorded as `2026-11-03T13:44:35Z`. It requires signed-in repository read access and may expire. Do not assume a historical Actions URL is permanent distribution.

| Bytes | Size | SHA-256 |
|---|---:|---|
| Released RC25 MSI baseline | 43,776,886 | `69ff4124531bf2575c875ae70e6bb05f9cd1db8f377aec13ee3fbb1ff53bd52d` |
| RC26 artifact ZIP | 46,312,841 | `9f7f23609dcee3b409e82f4b93e4f43b86cd7fe4f6baacdf83a16212f71aac9f` |
| RC26 MSI | 43,776,886 | `22bd2529e316ad5e56f1fe26faab6fe078dc0a5707dd30e62467edd8afedaf91` |
| RC27 MSI | Not built/verified yet | Pending Windows qualification; do not invent or reuse a prior hash |

The RC26 ZIP's installer path is
`AmplifaiPhoneInstaller/AmplifaiPhone-rc26-unsigned.msi`; the other folders contain
qualification results. The planned RC27 artifact is `windows-rc27-qualification`
with `AmplifaiPhoneInstaller/AmplifaiPhone-rc27-unsigned.msi`. Verify its actual
contents, identity, byte count and checksum after CI; planned names are not proof
that an artifact was created.

## Website compatibility, release isolation and rollback

The website's exact-client allowlist is in backend
`src/lib/release/phone-platform-setup.ts`. The last verified live deployment at
`https://amplifai.satoris.ai` used backend commit
`ac99dfccdd361875f56ea5aa23ee7f5e0cf2ba12`, based on
`41618496abe5baf612a169488f25d0857f55ac7d`. Its runtime change added only the
exact Windows RC26 tuple. Read-only served-code checks accepted exact RC26 and
existing RC25/Android identities and rejected mixed identity fields. Related
source tests had 139 passes; no live phone upload was part of that verification.

RC27 must receive a separate narrow admission change for app version
`2026.10.27-rc27` / build `26102727`, retaining bundle
`ai.satoris.amplifai.phone.windows-candidate`, `windows-x64-test` channel and
Windows x64 platform identity. Preserve RC26 and all earlier supported clients,
Android, shared courses, public download links and signed feeds. Test discovery,
create/deep-link binding and mixed-identity rejection before an approved deployment;
verify the exact deployed commit and served admission afterward without uploading
phone data.

The public Windows download/feed still advertise released RC25. RC26/RC27 are
distinct diagnostic candidates. Keep update signing keys, channel, public key,
feed URL and release controls unchanged. Admission is not a production app release.

If an RC27 admission deployment must be rolled back, return to the verified RC26
compatibility baseline `ac99dfc` or narrowly revert the RC27 tuple while preserving
the RC26 entry. Recheck existing identities and feed bytes. A later main-branch
backend deployment must not accidentally discard the separate RC26 admission
change. Do not remove existing support as an incidental rollback.

For the app, do not overwrite an old artifact or force a lower MSI version over
an active run. Version ordering and downgrade refusal remain protective behavior.
If a new app repair is required, build another distinct candidate with documented
custody rather than changing RC27 bytes in place. Preserve evidence and owned
session rules; never delete original Apple/phone data as a rollback step.

## Physical manual test criteria and remaining work

Before requesting another full phone transfer:

1. Publish the reviewed candidate branch and record exact remote commit and CI URL.
2. Require terminal Windows success, six frozen protocol cases with matching code
   hashes, normal packaged runtime-check, encrypted worker checks and the RC26 →
   RC27 upgrade/session-preservation receipt. Read the actual receipts, not only
   a green workflow badge.
3. Record RC27 artifact ID, expiry, filename, size and independently verified
   SHA-256. Keep the installer explicitly labelled unsigned diagnostic/test.
4. Obtain and verify the exact RC27 website admission deployment separately.
5. Let the tester install the verified candidate manually. Confirm app title and
   MSI identity are RC27. Do not install, bypass a security prompt, change backup
   encryption, rerun collection or enter a password on the tester's behalf.

The physical test must then establish the whole sequence: approved account/course
binding → USB unlock/Trust as needed → encrypted backup/password when required →
backup reception → source reading → contact selection including Unicode names →
reviewed selection → explicit authorized account Save → matching durable receipt
→ report reopen/reload. Keep the phone/cable connected as instructed. Backup
password, screen-unlock passcode and Apple ID password are different credentials.
Never collect or retain any of them in engineering evidence.

If the run fails, keep the app open and record only version, code, stage, UTC time,
safe exception class/review step, exit code and cleanup state. A failure during
collection does not establish account Save, and a temporary-data check is not
permission to remove evidence. Do not start another long backup before examining
the retained result. Encrypted backup improves available call-history coverage;
it does not guarantee every historical call exists on the phone.

Update this document before marking the candidate ready with the actual RC27 CI
URL/result, source commit, frozen receipt/custody result, MSI checksum/size,
upgrade result, exact admission deployment and the real-device/account-Save
outcome. If any remains pending, keep it visibly pending.

## Deferred navigation defects

These were observed separately and are not repaired by the Unicode change:

- Before native account binding, **Open my account** opens generic
  `/phone/account?week=phone`. It carries no iPhone/Windows setup context. The
  website initializes an empty phone/computer wizard, with per-tab
  `sessionStorage` and an explicit Resume path. A new tab can therefore repeat
  phone/computer/install questions even while a supported app is already running.
- RC26 is newer than the public RC25 feed. The updater correctly refuses that
  downgrade, but reports a failed update check, which the website repeats. Exact
  supported-client admission does not require updater success. Future UI work
  should distinguish “newer diagnostic build; keep using it” from a failed
  collection without relaxing downgrade protection or fresh collection approval.

Treat those as a separate coherent navigation change. Preserve one-run collection
approval, exact client/account/course binding and explicit destination/Save
confirmation; removing security checks is not a valid shortcut for repeated setup.
