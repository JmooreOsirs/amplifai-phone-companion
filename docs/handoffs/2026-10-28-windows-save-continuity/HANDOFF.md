# Windows/iPhone Save continuity — 2026-10-28

Status: RC28 package/upgrade, source controls and guided source/frozen Windows UI
qualification all passed at exact source4caa24f. Both artifact sets were independently
verified. Jeff merged/deployed the website fix; the read-only observation is below.
No agent production release/deployment or new physical-device pass is claimed.

## Start here

Three separate continuity defects remained after the RC27 Unicode fix:

1. Native Reopen account was enabled before binding the reviewed selection. An
   early click opened collection-management controls; a later click on the same
   button opened the correct Save continuation.
2. Windows browser-mediated page saving stopped for manual recovery after a
   temporary I/O failure. It did not use Android's bounded transport retries.
3. After the browser and native app acknowledged Save, the parent website cleared
   the completed preview before reading its report. Saved counts disappeared and
   generic status again said it was counting/waiting for the native acknowledgment.

The scope is those three defects. Do not remove collection approval, owner/course
checks, immutable reviewed selection, saved-page verification, lifecycle fences,
or deliberate report-review/course-completion steps to make the UI appear simpler.

## Reconciled bases and preservation

- Companion base: `e58fed8253cf661074f9dd6637deaa94d8b4b521`, the merge of PR49
  into `release/native`. The prior fix branch was no longer the right base.
- Jeff's `b7b6227159716e0786e70817fb089d686561b569` (exact RC27 signed update
  handoff pins) is an ancestor of this base and is preserved.
- Website base: `7d65918ebf2e43e1b20f18de8dc3511c918d2b65`, current main and
  the inspected production deployment at the start of this work.
- Local companion branch: `codex/windows-flow-continuity-oct28`.
- Local website branch: `codex/windows-save-continuity-oct28`.
- Current RC27 public installer, signed feed, signing key, bundle identity and
  update handoff remain unchanged. Do not reuse RC27 identity for a changed MSI.

The previous [RC27 handoff](../2026-10-27-windows-iphone-unicode/HANDOFF.md)
contains the UTF-8 failure history, frozen Windows proof, artifact identity and
Jeff's subsequent update handoff. Do not confuse its historical RC25 public-feed
observations with current RC27 activation.

## Evidence and confidence

A real RC27 Windows/iPhone run passed the earlier post-capture failure boundary,
completed selected-source saving, received the matching native account Save
acknowledgment and displayed a matching report. Aggregate-only production readback
verified complete contiguous page sets and equality of parsed and stored counts
for every selected category. That verifies one selected run, not all historical
phone records, every phone/iOS version, or semantic deduplication across runs.

The tester reported an SMS interruption and manual restart outside the recording.
Receipts showed a multi-minute gap followed by complete saving under the same
transfer identity. Available filtered server logs did not retain a causal event.
The original request/exception remains unknown. The retry change addresses a
reproducible recovery-policy gap; it must not be described as a forensic diagnosis
of that interruption.

The visual evidence also showed the early management-route fallback and the
post-acknowledgment counting regression. Those have deterministic source causes.
Private recordings, owner/run identifiers and real record counts are intentionally
omitted from this public engineering note. Tests use fictional data only.

## Design and invariants

### Native continuation

The original Continue gesture owns one exact run/review/selection continuation.
Preparation must not expose a Save action that falls back to collection controls.
Browser return is allowed only after the server-verified handoff is bound, current
authority is known, the same immutable selection remains active, and no browser
receipt/finalization/stop supersedes it. Uncertain bind replies keep the exact
request for the existing explicit confirmation recovery; they do not open a tab.

Opening a browser is navigation, not evidence of account Save. Source delivery and
the final matching receipt remain separate. A failed launch leaves a safe primary
continuation. Even an accepted OS launch can open another profile or a closed tab:
the separate quiet **Account tab missing? Open again** action reopens only the same
verified URL. It is gated by current readiness and receipt state, with launch
reentrancy protection and a one-second double-click fence. Automatic and repeated
callbacks must not multiply opens. Existing browser destination progress suppresses
an unnecessary automatic return.

### Windows page recovery

The phone manifest's `iphone` platform does not identify its computer host. New
recovery is selected only for an existing verified run with
`phone-run-windows-v1` disclosure and an iPhone manifest. Mac/legacy bypass and
Android's existing policy are preserved.

Retries occur only at named page-read, page-upload and receipt-confirmation I/O
boundaries. The budget is two retries, after500ms and1500ms. Exact known temporary
service errors may be classified explicitly; unknown HTTP errors, invalid data,
changed owner/approval/consent/retention, integrity conflicts, cancellation and
expired authority remain stopping conditions.

The same preview, transfer key, collection binding, retention binding and page
identity must survive retry. Confirmed prefixes are checked against server receipts.
A lost reply after commit must return/reconcile the same receipt. Fresh-generation
first-page reconciliation remains mandatory; a non-replayable zero-row lifecycle
write must not acquire automatic replay merely by using the retry wrapper.

No source-import SQL, schema, server permission or uniqueness constraint is
weakened. Synthetic tests must distinguish attempts from committed pages.

### Acknowledged Save and report loading

Once exact source proofs and the native acknowledgment are observed, the completed
count/receipt snapshot must remain visible while the matching report is read.
Report-read retry must not restart source saving or wait for an acknowledgment
already observed. Late results from a different owner/course/run/review must not
replace current state. New review or consent/lifecycle invalidation must clear the
old completed marker.

Course progress is separate: source pages saved does not imply the user reviewed
the report or completed the course segment. Preserve that distinction.

## Required regression matrix

| Area | Required observations |
|---|---|
| Binding race | Delayed bind has no enabled incorrect continuation; successful exact bind opens correct route once. |
| Repeated gestures | Repeated Continue, repeated callbacks and reentrant browser launch cannot create duplicate opens/Saves. |
| Authority | Uncertain/rejected/expired/stale run or changed selection cannot open a continuation. |
| Browser failure | Failed OS launch leaves verified manual fallback; navigation is not a Save receipt. |
| SMS interruption | A synthetic temporary page failure resumes the same transfer and complete ordered page set. |
| Lost reply | Simulated commit then lost response reconciles existing receipt, without another committed page. |
| Exhaustion | Only the finite retry budget is spent; manual recovery preserves confirmed prefix. |
| Hard stops | Consent/owner/lifecycle/integrity/refusal changes do not receive transport retries. |
| Empty/fresh data | Non-replayable zero-row boundary and fresh-first-page lifecycle reconciliation stay intact. |
| Report loading | Delayed/failed report preserves Saved counts/acknowledgment; retry reads report only. |
| Stale results | Owner/course/run/review change rejects late completion and old report results. |

## Validation ledger

Final local results on 2026-10-28:

- Companion exact existing CI aggregate: **520 run,509 passed,11 platform skips**.
  Ten new continuation regressions live in existing `test_native_app`, so no CI
  workflow change is needed. Focused native set: **72/72 passed**.
- Actual isolated Mac Tk source controls/geometry: **21/21 cases**, at1180x820
  and680x680 with4/3 scaling, plus680x680 at1.75. Preparation, bound, browser-opened,
  received, pending receipt, saved and guarded manual recovery were covered.
  This is source Tk evidence on Darwin, not Windows OS/frozen qualification.
- Companion release-control checks: **42/42 passed**.
- Website related aggregate: **1,009 passed,2 skipped**,69 files total with68
  passing and1 skipped. The aggregate includes Windows recovery, mounted account
  completion, legacy/Mac/Android compatibility and existing report/run checks.
- Focused Windows retry/related suite: **87/87 passed**, including37 new Windows
  cases. Synthetic server receipts distinguish request attempts from commits.
- Completed-Save suite: **40/40 passed**, including5 mounted account-page cases,
  23 visual/component checks and12 journey-state checks.
- Changed website files pass ESLint with zero warnings. Focused transitive
  TypeScript check passes without declarations/stubs or dependency installs.
- Full website typecheck still reports the **same26 diagnostics, byte-identical
  to untouched7d65918e**. No new type diagnostics were introduced.
- One existing profile-navigation test expected the old report title. Its failure
  was independently reproduced on untouched7d65918e; only that heading assertion
  was updated to the actual **Phone Summary** heading. Snapshot/Done guards remain.
- Independent source review found no remaining material issue. Both diffs pass
  whitespace checks. Jeff's signed fixture/workflow pins, version identity, feeds,
  website release/admission configuration and database schema are unchanged.

Local runtime: CPython3.12.14, Node24.20.0 and available Vitest5.0.1. No install,
Windows CI dispatch, real-account upload, phone operation or deployment occurred.

Reproduce from the respective repository roots with an existing locked runtime:

```sh
PYTHONPATH=source/collector:source/collector/tests:source/collector/windows:source/collector/windows/tests:source/script \
python3.12 -m unittest test_helper_protocol test_capture_runtime test_encryption_preparation test_helper_boundary test_agent test_ios_backup.IPhoneInterfaceTest test_metadata test_native_setup test_setup_delivery test_native_run test_native_app test_native_bridge_boundary test_destination_bridge test_sqlite_encrypted_size_compat test_sqlite_messages_size_compat test_windows_source_diagnostics test_source_diagnostics test_source_diagnostic_flow test_updater test_update_handoff test_session test_failure_diagnostics test_gui_guards test_video_parity test_companion_status test_windows_candidate test_windows_msi test_backup_stream test_journey test_pump_cadence -q
python3.12 -W error -m unittest discover -s release_control -p 'test_*.py' -v
```

```sh
env -u AMPLIFAI_NEON_DATABASE_URL -u DATABASE_URL -u AMPLIFAI_LOCAL_PG \
node node_modules/vitest/vitest.mjs run tests/phone-*.test.ts tests/windows-transfer-retry.test.ts --exclude '**/*local-pg*'
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

The source aggregate intentionally excludes disposable-local-Postgres suites.
No SQL/schema changes were made; new retry tests use an isolated synthetic
committed-receipt store with production protocol/hash/checkpoint validation.
Do not describe those new tests as real-database or real-phone acceptance.

The existing local website dependency tree has missing CRM packages; an untouched
7d65918e worktree was typechecked separately to distinguish baseline errors from
new errors. Do not install dependencies or claim a clean repository-wide typecheck
without evidence. Keep environment limitations explicit.

## Release boundary and initial candidate plan

The original implementation phase covered local source changes and synthetic
tests. Publication of both fix branches and Windows candidate qualification was
subsequently approved on 2026-10-28. The latest instruction assigns deployment to
Jeff. No agent merge, production app release, production deployment, PC installation,
phone backup or real-account upload is part of this handoff.

During the local-only phase, the planned Windows candidate needed a distinct version/build/MSI
identity and Windows frozen/UI/upgrade qualification. Read-only metadata inspection
found Windows `2026.10.28-rc28` / build`26102828` / MSI`1.0.28` unused at the time;
this was initially a proposal. The subsequently assigned and qualified candidate
is recorded in the publication follow-up below.

Publication scope should name the public companion branch/source, private website
branch and Windows CI. Production website behavior deployment and exact candidate
admission require their own explicit scope. Preserve current RC27/older support,
Android, public downloads, signed feeds and Jeff's pinned update handoff. Candidate
preparation does not require replacing the production app release.

Software tests cannot establish actual default-browser activation, wrong-profile
recovery, physical iPhone collection, consumer MSI upgrade or a new real-account
Save. Preserve those as later acceptance checks instead of calling a local source
test a consumer-ready installer.

## Published RC28 candidate and deployment handoff

- [Companion draft PR50](https://github.com/JmooreOsirs/amplifai-phone-companion/pull/50):
  native correction `dc598c43d7db89935515b89f241ea52655a6bf22`, candidate preparation
  `4caa24fdeb9a88d440eb9dc0e7b3b899e588beae`. Qualification uses exact source4caa24f.
- [Private website draft PR45](https://github.com/JmooreOsirs/amplifai-database-engine/pull/45):
  the Save/report fix was rebased onto newer main
  `35146ec8654bf765d96589a5a670fd22dd20922c`, preserving the merged Final-report work.
  Rebased fix `65eb7adc266465445e96401f0cf519dc1b168294`; exact RC28 admission
  `eedd3e941b19fc8627f1a90a5ccfce862356d491`.
- RC28 identity: `2026.10.28-rc28`, build`26102828`, MSI`1.0.28`, unchanged
  Windows test channel, bundle ID and UpgradeCode. Existing RC27 download/feed
  bytes and Jeff's exact RC27 signed handoff inputs remain unchanged.
- Website runtime admission adds only the exact RC28 tuple alongside existing
  Windows/Android pins. With the new admission and current35146ec base, the local
  related aggregate passed **1,014 tests,2 skips**; focused admission tests127/127
  passed. Full TypeScript diagnostics remain the same26 as untouched35146ec.
  Existing Vercel integration automatically built successful PR previews; none
  was promoted to production by this work.

### Exact Windows package qualification

[Package run40720333063](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40720333063)
passed at4caa24f, completed2026-10-28 16:02:07 UTC:

- **520 Windows source tests:514 passed,6 platform skips**.
- Full-size encrypted Messages extraction and exact byte geometry passed.
- Actual frozen encrypted worker and the six-case CP1252/Unicode protocol proof
  passed. The isolated test launcher remains excluded from the MSI.
- MSI build, installed inventory, protocol registration and exact released
  RC27-to-RC28 in-place upgrade passed on the disposable Windows runner. The
  session-data sentinel survived upgrade and uninstall. This is not a consumer
  installation or physical-phone pass.
- [Source release controls40720204427](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40720204427)
  passed at the same source, including native model/hidden AppKit/bridge checks
  and release controls.
- [Guided source/frozen UI40720338664](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40720338664)
  passed, completed2026-10-28 16:20:53 UTC. The state-boundary set ran203tests:
  202passed,1platformskip. All208main source/frozen screen pairs matched their
  compared geometry, resolved styles, lifecycle and selected semantic fields.
  Independent readback verified480PNG hashes (416main screenshots plus64nested
  guide images),27source-proof files and the package's matching GUI source hash;
  zero comparison failures. Critical preparing/opened/pending/saved frames were
  visually reviewed. At680x680 with1.75scaling, optional missing-tab recovery lies
  partly below the initial fold; scrolling is expected and keyboard checks confirm
  it becomes fully visible when focused. Do not claim every control fits unscrolled.
  [UI artifact12832222692](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40720338664/artifacts/12832222692)
  is17,101,615bytes, SHA256
  `9b758be3f6ad3085773917020cd86fca81dfa16c29fb2b220142ea7476f95f0c`.

[Verified Actions ZIP](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/40720333063/artifacts/12831142246),
artifact`12831142246`, `windows-rc28-qualification`, expires2026-11-04 16:01:57 UTC:

- ZIP:46,327,715 bytes; SHA256
  `b96a43ecf9d421058bdae7d70b1b27f5fb1266fbbade04a20f739cbc4f0d0c02`.
- Installer inside ZIP: `AmplifaiPhoneInstaller/AmplifaiPhone-rc28-unsigned.msi`.
  Size43,785,078 bytes; SHA256
  `d78576276c9031fbfd39b9c980ea23c66b471609a863e6f85277d9eee1041768`.
- Independent readback verified safe ZIP extraction, actual ZIP/MSI bytes, package
  inventory,19 production GUI source hashes,8 production source/canonical-code
  hashes, helper hash and receipt crosslinks. MSI version is corroborated by the
  actual Windows install log and receipts; no MSI/helper was executed locally.

### Jeff's release boundary

Jeff received the PR links privately with an explicit instruction to deploy only
after remaining qualification gates pass. No recipient details or conversation
contents belong in this public note. No further handoff text is required merely
because CI finishes; PR status/notes carry the qualification results.

GitHub readback identifies Jeff (`JmooreOsirs`) as the merger of website PR45 at
2026-10-28 16:21:43 UTC, after guided UI passed. Merge source:
`a2ca89abe51684fcbeb4cbcb9ea1b179a89a9bda`. This preserves intervening main work,
including the enrollment-save visibility correction in PR46. A subsequent
read-only Vercel check resolved `amplifai.satoris.ai` to READY production deployment
`dpl_GZn8rCr5awhApmyd2dJ1DYpKVeJJ` at exacta2ca89a. No deployment/promotion API
was called by this work. Metadata readiness is not authenticated account/phone
acceptance; no new real collection or account upload was performed.

Deploy the reviewed website behavior and exact admission after qualification,
preserving current-main unrelated changes. Do not roll back to historical7d65918e
or2990e5c and thereby remove later work. Record the actual predeployment source and
deployment ID, then roll back only the scoped change or that exact prior deployment
if needed. Keep all existing client pins, public downloads and signed feeds.

Candidate artifact delivery does not require publishing an RC28 production app
release or changing update feeds. An RC28 candidate may refuse the still-older
RC27 signed feed; this is a downgrade refusal, not permission to weaken verification
or switch the production feed. Historical `phase=handoff` remains pinned to RC27:
do not dispatch it from RC28 source. Any production RC28 release requires its own
corresponding source/signing/handoff/public-byte verification.
