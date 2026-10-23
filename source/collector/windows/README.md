# Windows companion source candidate

This wrapper presents the local iPhone collector on Windows. The published unsigned TEST baseline is RC21 (`2026.10.22-rc21`, build `26102221`, MSI `1.0.21`, native source `efffc39d0012ec49e8530cd8412435d87a977163`). A runner-user install, released artifact, physical PC/iPhone test and authenticated account Save remain distinct evidence.

## RC22 candidate — October 23

The source candidate is `2026.10.23-rc22`, build `26102322`, MSI `1.0.22`. It is not a released or admitted build until its exact qualification and publication receipts are recorded.

Plain launch opens an account-led start screen. Connect phone in the authenticated account delivers the existing setup-v2 link, exact Windows disclosure and immutable account/course approval. A separate Start admits collection. The four visible steps are Approve, Collect, Choose and Save & report; only the current stage is presented, with Details & recovery available separately. Existing in-flight legacy review remains recoverable without a new backup.

Contact cards retain the bounded 200-contact page and cross-page selection model. Deliberate Continue pins the run UUID and exact selection, waits for the helper's matching review UUID/count/digest, and survives an overlapping authority refresh before dispatch. Cancellation, expiry or changed selection cannot carry that intent into another review. The original account tab receives the bound selection; the fallback return URL includes the approved enrollment and current run UUID. Paused/refused/expired authority, preview received, pending receipt and matching saved acknowledgment have distinct states. No navigation or progress counter grants approval or establishes a saved report.

Backup file telemetry now uses the existing 0.5-second throttle and exact final flush. The Windows helper drain runs every 50ms, while unchanged visual state and handoff polling keep a one-second heartbeat. This removes measured pipe backpressure; it does not reduce the device's full-backup wire payload or establish a customer's historical Save duration.

Required candidate evidence: actual Windows source/frozen guided state renders and resolved styles, keyboard/text-scale checks, event-loop measurement, full-size shared helper/frozen worker, RC21 MSI replacement and signed update handoff. Physical phone/account acceptance stays separate. Windows remains unsigned TEST; the distribution signature does not add publisher trust.

## RC20 supplemental native/bridge boundary test

`test_native_bridge_boundary.py` joins the production Windows `NativeRun` to the real shared `BridgeServer` over ephemeral loopback HTTP. It reuses the existing pinned synthetic setup exchanges, disk-backed capture and HTTP client; only remote authority replies are replaced. Claim, exact `phone-run-windows-v1` approval, separate Start, reviewed Continue, current status, immutable bind and destination verification precede v3 release. Lost claim/approval/Start/Continue/bind/consume/local-release replies preserve the exact request or transfer token. Changed selection, handoff, digest and release proofs are denied; source pages remain blocked before release and during uncertain authority. Selected rows and page/manifest hashes are checked through actual HTTP responses.

The fixture explicitly asserts `received` before any synthetic Save ACK, then `saved_pending_browser_receipt` before final confirmation. Those are **synthetic protocol ACKs only**, not account persistence or report readback. Completion clears native capability references; teardown verifies the listener, timer, socket and temporary record/transfer files are removed. Fixed clocks keep the scenario bounded. No UI mocks, Tk, helper subprocess, device or hosted service is used. The synthetic build identity is not an admitted release.

```sh
PYTHONPATH=source/collector:source/collector/windows:source/collector/windows/tests:source/collector/tests:source/script /tmp/amplifai-rc29-test-venv/bin/python -m unittest test_native_bridge_boundary -v
```

The combined fixture passes locally on macOS/Python 3.12.12. This test-only supplement leaves RC20 production bytes unchanged; it does not rebuild or qualify a Windows package, activate server admission, or establish real-phone/account Save acceptance.

## October 21 compatibility history

This source phase aligns the Windows presentation state with the existing shared bridge. A server-authenticated replacement of an unconsumed account intent retires the prior intent and requires a fresh native decision. Repeated identical pending intents preserve their decision; changed labels for the same intent remain invalid. Late pending/approval/denial responses cannot revive a retired intent, and a native dialog answer applies only to the handoff and intent it displayed. A rejected in-flight destination decision keeps the exact local review open for the next authenticated status poll. Received data and either Save receipt block destination replacement.

The shared helper retains authority, release and data-integrity enforcement. `run-authority` is a native-to-helper command; refreshing/paused HTTP behavior and identical `/v3/release` retries are implemented by the shared bridge. There is no separate authority-refresh or uncertain-release helper event to add to this wrapper. The additive Windows setup adapter is being implemented against the frozen contract below; server activation and qualified build admission remain pending. Source diagnostics already consume the shared strict allowlist; diagnostic-only SMS facts never become recovered messages or readable zero. Call exclusion accounting remains private helper history, outside the browser payload.

### Native setup contract checkpoint

The Windows adapter targets `phone-native-setup-v2` revision 1, pinned at website commit `8214edaf5ff547b300ef4610cbc4686bb368ba63`. Normative JSON SHA-256: `d2ff30a92fab809960dcfe0520e08585a7437026ab6c422deb66f78012984ac8`; normative Markdown SHA-256: `e7b738fc38609e97b014d37f6969605a2d16a46def062e2bfce4b4204b02c7b7`. The Windows disclosure is `phone-run-windows-v1`, SHA-256 `795c0423c9c15c4c13e79145e75b93f42e329b53acd5c10d5eb387fa48cc0f23`, with the additive `phone-platforms-2026-10-v1` supplement. Archived account notice v7 and Mac v1/v3 behavior remain unchanged.

`native_setup.py` validates strict deep links, independent canonical capabilities, exact client/disclosure/scope fields, opaque snapshot binding and matching unstarted preparation receipts. Claim and approval requests retain fixed bytes and hide capabilities in their representations. `native_run.py` adds fixed-origin bounded TLS transport and memory-only claim, approval, separate Start, reviewed Continue, immutable bind and destination verification. Lost replies pause authority and retain the exact original request; stale callbacks cannot authorize collection or a superseded intent. Browser-session setup status/revoke are not native routes.

Headless parser/transport/state tests use the pinned synthetic Windows exchanges in `tests/fixtures/native-setup-v2.json`; those versions/builds are not admitted releases. Timestamp checks allow at most 60 seconds of server/device skew. Monotonic deadlines cap setup and prepared-run lifetimes from their original requests, including shorter server-issued run lifetimes; replay and clock rollback cannot extend them. Server expiry remains immutable and each transfer still requires current authority. `setup_delivery.py` routes one strict setup URI through a Windows message pipe. Its protected DACL and namespace use the current logon SID, remote clients are rejected, and first-instance ownership prevents a second primary. One pending UI admission expires after three seconds; client I/O and cancellation are bounded. Capabilities stay in process memory. Delivery ACKs indicate only UI admission. The pipe follows [Windows named-pipe access rules](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights) and [overlapped I/O lifetime rules](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-server-using-overlapped-i-o). Ten headless routing/configuration checks pass; four real-kernel cases require Windows:

```powershell
$env:PYTHONPATH = 'source/collector;source/collector/windows;source/collector/windows/tests;source/collector/tests'
python source/collector/windows/tests/qualify_setup_delivery.py --output artifacts/NativeSetupDelivery
```

This emits `setup-delivery-receipt.json` and exits nonzero on any failed gate. It checks the actual protected logon DACL, primary exclusion, plain launch routing, duplicate/busy delivery, timeout/late-admission denial and cancellation/reacquisition. An existing companion causes a visible failure; the fixture never replaces it. It creates no authority transport or collector. The kernel-pipe gate passed on actual Windows 2025 / Python 3.12.10, run `39693607525`, source `8cfb326965e614a89825e21e35cadfb0d2ef81a8`: four tests, no failures/errors/skips. Artifact `12389682601`, 870 bytes, SHA-256 `c28729d52688c12deb8b2966e3d145be4194ea74622d1a8e2d095061492b3c2e`. This proves the bounded synthetic IPC layer, separately from the changed UI and frozen package.

The installed entrypoint now accepts plain launch or one exact setup URI. Plain launch owns the delivery pipe; another launch forwards to that primary and exits. Busy/expired/closed delivery produces a visible recoverable failure. The wired Windows app advertises the exact schema-2 v2 capability response; Mac schema-1/v1 and legacy status remain unchanged. No browser-facing mutation route was added.

Setup displays the authenticated account/course/categories and pinned disclosure in the existing read-only text primitive, with fixed agreement and Agree/Start/Retry/Cancel controls. The original native theme, fonts, colors, spacing and contact workspace remain the design authority. Agreement does not collect: a separate Start must receive exact current admission. Continue freezes the reviewed selection before pairing; a lost bind reply retries the same immutable handoff/digest. Current authority and exact destination verification are required before native destination approval. A replaced authenticated intent retires its old response. A handoff that expires after binding requires recovery or a new explicit setup; it cannot silently rebind.

Received preview, pending browser receipt and matching saved ACK remain distinct. Only the matching saved ACK finishes the helper. It drops setup/run capability references and keeps only the non-secret enrollment/handoff/digest completion binding for Open results. Cancel stops local work first; the original signed-in account tab owns server setup revoke. Authority/capabilities and pending requests remain in memory only. Legacy plain-launch collection, separate website Save and ephemeral cleanup remain available. Build identity and unsigned TEST status are unchanged; backend admission is still pending.

Normal Continue asks the person to return to their original account tab without opening a URL automatically. Automatic browser activation is unavailable in this candidate. Explicit **Continue in browser** uses `/phone/account?continue=reviewed&enrollmentId=<uuid>&week=phone`; **Account controls** uses `?phoneRunControl=<uuid>&enrollmentId=<uuid>&week=phone`; **Open results** and expired-handoff recovery use `?enrollmentId=<uuid>&week=phone&completedHandoffId=<uuid>&completedPayloadSha256=<digest>`. The latter two never include `continue`. Every identifier is validated; no capability goes in a browser-return URL. Explicit recovery may open another tab/profile and still requires the correct signed-in account. It cannot pair, rebind or silently change the frozen selection. Setup Retry also resumes an uncertain Continue using that exact selection.

The changed source passes 203 local Windows-wrapper tests (198 passed, five platform skips) using the existing macOS Python environment. The affected shared status/bridge suites pass 42 tests (40 passed, two platform skips). No Tk window was launched on macOS. The explicit fixture is isolated under tests; production URI and CLI dispatch cannot select synthetic transport:

```powershell
$env:PYTHONPATH = 'source/collector;source/collector/tests;source/collector/windows;source/collector/windows/tests;source/script'
python source/collector/windows/tests/render_contact_workspace.py --source source --logo source/public/brand/amplifai-by-nexus-original.png --output artifacts/AmplifaiNativeSetup --native-setup
```

Run on the existing disposable Windows desktop with sufficient measured work area, using the existing display-preparation/restoration gate. It emits `render-native-setup-receipt.json` and 33 `windows-native-<width>x<height>-scale-<scale>-<state>.png` captures: approval, approval-uncertain, prepared, start-uncertain, contact-review, continue-uncertain, bind-uncertain, browser-return, pending-receipt, saved-recovery and long-scope-error at 920×749, 680×680 and 680×680/1.75 Tk scale. It checks visible desktop/work-area bounds, computed native styles, fixed actions while text/list scroll, full disclosure, keyboard traversal and actual Tk button invocation through exact approval/Start retries, uncertain Continue recovery through Setup Retry, and local cancellation. It creates no network authority transport or collector. Source callback fixtures additionally reject wrong scope, stale intent verification and early cleanup. Unchanged prior layout and IPC evidence need not be repeated.

The first changed-screen run, `39696185425` at `6a08415`, passed its unit gate and captured ten 920×749 states, then failed the immediate long-text scroll assertion. The fixture now forces Tk’s documented `count -update -ypixels` calculation before `yview`, because wrapped-line heights are cached asynchronously ([Tk 8.6 text contract](https://www.tcl-lang.org/man/tcl8.6/TkCmd/text.htm)). It still requires real overflow, positive movement, the full scope/error text, a visible final character and a fully visible final line. Each setup capture records cached/current pixel heights and before/after scroll fractions; a remaining scroll failure saves `native-scroll-failure.json` and a visible-desktop failure screenshot before failing. Actual Windows run `39697119038` at `3b3472a` confirmed the correction: 33 screenshots and 75 tests passed. Artifact `12391158284` has SHA-256 `37830c5bdbd4d515934f2ac6479665c2afa9b32f3b9dfe54f02669fc6827ce86`. Scroll receipts distinguish the stale cached height from the synchronized wrapped height.

Visual review of that run found a separate disabled-state defect: the new agreement checkbox inherited clam’s light-gray disabled surface. `Native.Disclosure.TCheckbutton` now derives from the existing disclosure style and maps only disabled background/foreground to the existing `BACKGROUND`/`MUTED` tokens. Legacy consent, fonts, wrapping, geometry and normal-state colors are unchanged. The computed-style gate records and checks both maps plus disabled, selected-disabled, active-disabled and focused-disabled resolutions. Eighteen focused headless tests pass; this color correction requires the changed setup matrix to run again.

Changed native rendering/button execution, frozen helper/MSI/protocol launch, installed update, public-byte verification and real phone/account Save/ACK/report/return-login acceptance still require their own qualification. Synthetic fixture identities remain unadmitted; local tests cannot activate production authority.

Source verification used an existing macOS Python 3.12.12 environment with `pymobiledevice3==10.4.0` and `cryptography==50.0.1`, with synthetic records and ephemeral loopback HTTP only. The affected Windows suites passed 69 tests; the shared contract suites passed 91. The supersession/rejected-decision tests failed on the old Session, and the dialog regression reproduced the old callback approving a replacement intent. Python compilation and `git diff --check` passed. These results do not replace the Windows Python 3.12.10 frozen-runtime and native-render gates.

Run from the repository root with the collector test dependencies available:

```sh
PYTHONPATH=source/collector:source/collector/windows:source/collector/windows/tests:source/collector/tests python -m unittest test_session test_gui_guards test_engine_wire test_windows_source_diagnostics test_destination_bridge -v
PYTHONPATH=source/collector:source/collector/tests python -m unittest test_bridge_recovery test_bridge_refresh test_bridge_ack test_source_diagnostic_flow test_call_accounting test_backup_watchdog test_sqlite_encrypted_size_compat -v
```

## Local collection and review

New collection requires the account-bound setup-v2 approval, a clean temporary-data inspection and current Start admission. The old local agreement cannot start another collection. iPhone Trust and encrypted-backup password prompts remain user decisions; the password travels only over the private helper pipe and is cleared from the entry widget. No backup, password, message body, or attachment is sent to the browser.

The helper uses the shared disk-backed sanitized record store. The window keeps only the current 200-contact preview page plus selected contact IDs. Search, Next, and Previous preserve selection across pages. Review sends at most 1,000 IDs per command, with a new UUID and SHA-256 over the complete ordered ID set. The exact review ID, page cursor, selection count, and digest must return before pairing is offered. During a live handoff, contact edits are locked; Change selected contacts requests helper revocation and keeps the reviewed selection and handoff until revocation is accepted. The helper refuses replacement after a first or final account-save receipt, and late revocation cannot erase that receipt. A new pairing supersedes the old one only after helper acceptance; stale page, review, and handoff responses cannot grant a new one.

The browser's received state and saved parts awaiting final browser receipt cannot finish the native session. Only the matching bridge's verified saved state permits finish; the helper must then exit successfully and an empty marked-residue inspection must complete. A fresh pairing replaces an unsaved old bridge only after helper acceptance. The five-minute deadline applies to initial admission; an authorized active transfer uses the shared v2 keepalive and durable ACK contract. Request stop asks for explicit local-review discard and does not imply account parts were erased.

The wrapper provides all-page Select all/Clear all and native approval of the server-confirmed account destination before code-free Connect. The helper supports the fixed-origin `/v3/discover` contract; the temporary one-use code remains an explicit recovery path. The rc17 baseline includes a current-step Apple Devices guide, read-only On/Off/Unknown backup-setting check, and source details without changing account payloads or consent. It still needs distinct package and actual-PC qualification.

The window accepts only the helper's bounded transfer counters; bytes checked, retained, discarded and locally processed are shown as measured counts, never as an invented time remaining or account-save percent. Optional Call/SMS failure reasons are fixed allowlisted codes, with Unavailable distinct from readable zero. The shared helper includes the pre-byte watchdog, encrypted SMS diagnostics and call exclusion accounting. Producer ciphertext digest, exact copy readback, absent sidecars and a bounded source-specific SQLite worker remain required for the supported shorter encrypted-main repair. Encrypted Messages alone also permits exactly one validated current SQLite page of growth, with full integrity/schema checks and the same guarded standalone copy throughout metadata extraction. Other growth remains rejected; this compatibility rule does not establish the historical mismatch's cause. Named SQLite integer-primary-key source IDs remain stable for Contacts and Calls. Required Contacts and global failures remain fatal; optional Call/SMS failure is Unavailable. Finalize and the per-source saved report live in the authenticated website after separate Save decisions, not in this window's capture progress.

Stop closes the helper's input cooperatively. It cannot promise interruption of synchronous capture. A separately confirmed Force stop targets only the owned helper, bypasses cleanup, and requires a new residue inspection. Failed cleanup is reported, never counted as completion or silently deleted.

## Signed update lifecycle and running status

The rc19 wrapper identifies itself as `2026.10.15-rc19`, build `26101519`, channel `windows-x64-test`, with MSI version `1.0.19`. The existing UpgradeCode `0C4BE78C-C5E1-4EA9-841B-4D8D93F45871` and per-user `%LOCALAPPDATA%/AMPLIFaiPhone` app path stay fixed. The existing `%LOCALAPPDATA%/AMPLIFai Phone Candidate/sessions` data root is outside the MSI payload.

After clean temporary-data inspection, a startup check verifies exact signed UTF-8 manifest bytes from the fixed `/phone/windows/update-v1.json` and detached `.sig` endpoint before parsing. The existing Ed25519 key and hash-pinned `cryptography==50.0.1` verify both manifest and installer. Manifest fields are exact; version labels are at most 64 ASCII characters, builds are nonzero 1–12 digit strings, MSI versions respect Windows Installer limits, and build/MSI must advance together. A 16 KiB manifest, 128-byte signature, 512 MiB installer, 10-second request timeout and 180-second download deadline bound each operation. Only tagged MSI assets under the fixed GitHub release repository are accepted; HTTPS redirects may use GitHub release asset hosts only. Installer byte count, SHA-256 and Ed25519 signature must all match.

Checking/download preparation never installs or stops collection. Explicit Open verified update installer requires live clean inspection, no running helper, no residue, no unsaved review, no unresolved pairing or Save/ACK. The gate is checked again after the confirmation dialog. The wrapper copies the complete existing frozen helper/runtime into its separate marked update cache. Build-time helper path/length/SHA-256 records are compiled into the frozen wrapper; both source and copied runtimes must match, with native file guards held through readiness. There is no source or system Python fallback.

The copied helper accepts only the fixed `install-verified-update --handoff-root` mode, independently verifies persisted exact manifest/signature bytes and the MSI, and holds native READ-sharing handles on its runtime, cache and MSI. It emits bounded readiness, requires explicit commit plus pipe EOF, and waits for the old companion process to exit normally before launching the fixed system `msiexec.exe /i` with normal UI. The wrapper releases its status server and closes normally after successful handoff; no installed app files remain pinned by it during replacement. Missing/tampered bootstrap, bad signatures or absent commit leave the old app open and never launch MSI. A receipt write failure after MSI starts cannot release guards before Installer exits.

Verified downloads use separate random temporary directories with bounded ownership markers and exact signed manifest bytes. Closed/cancelled preparation removes only owned inactive downloads. After Installer exits, the copied helper removes the verified MSI; its own mapped runtime remains marked until a later owning app can verify and remove it with helper/installer PIDs dead. Active/unknown helpers, unmarked files and phone/session roots are preserved. Native sharing blocks data edits and file/directory replacement; Win32 attribute-only access is outside sharing-mode restrictions, so unrestricted metadata denial is not claimed.

The rc19 workflow froze the matching wrapper/helper, rendered current and previous-run diagnostic states, ran the native history/DACL and update safety tests, and qualified exact released rc18 → rc19 MSI replacement with a runner-owned session sentinel. Its MSI, candidate inventory, render receipts, unit log and installation logs describe that release. A later candidate requires its own exact artifact and signed handoff qualification; historical rc18/rc19 artifacts and fixtures are never relabeled. Signing keys stay off CI. These remain runner proofs; normal customer policy and physical-phone/account acceptance are distinct.

The wrapper owns status port `127.0.0.1:48752` through the shared bounded companion-status server. HTTP callbacks enqueue update checks for Tk; they read only cached app identity/busy/update fields, never phone or account data. Another listener prevents collection in this window and requires closing the other companion and relaunching. It is never taken over.

### UI reference contract

The accepted Mac RC35 source at `dfc80751b8a4a5b16fb67833727130fb2ec3d44f` governs this candidate. The October 23 request supersedes the older Windows tab/Listbox composition. `brand_tokens.py` pins the Mac palette, 12/20/28 spacing, 10/12 card/panel radii and 220px minimum contact-card width.

| Windows element | Mac source authority and derivation |
| --- | --- |
| Header and four-stage journey | `ContentView.swift` and `Brand.swift`; current-stage indicators with collapsed recovery |
| Centered stage panels | `BrandWell` / `BrandControls.swift`; bounded native rounded content hosts |
| Contact checkbox cards | `ContactReviewView.swift` / raised cards; responsive grid with native keyboard checkbuttons, search, selection and fixed action dock |
| Collection measures | `CollectionStatusPanel.swift`; measured transport/processing counters and explicit incomplete account-save status |
| Setup approval | `NativeSetupView.swift`; exact account/course/disclosure, separate approval and Start, Windows Apple Devices guide |

The preferred window is 1180×820 with the existing monitor work-area fit and 680×680 minimum. Outer 28, panel 20 and block 12 spacing are resolved through the same Tk DPI factor; native control insets 16/10/8 are scaled adaptations.

The shipped OpenSans font has fixed SHA-256 `36643644f318a812aab2d2ed3bb98f8cf0872527f835fe9398d95fe6b9adb878`. It is registered privately for the Windows process; Arial is the explicit fallback. Body 12pt and heading Arial 21pt resolve to Mac logical 16/28px at 96dpi. The native raised-card solid fill is the Mac gradient's 10% heading/panel edge. OS-native checkbox and focus behavior are retained.

The current actual-Windows gate is:

```powershell
$env:PYTHONPATH = 'source/collector;source/collector/tests;source/collector/windows;source/collector/windows/tests;source/script'
python source/collector/windows/tests/render_guided_journey.py --source source --logo source/public/brand/amplifai-by-nexus-original.png --output "$env:RUNNER_TEMP/guided-source"
python source/collector/windows/tests/frozen_guided_fixture.py --source source --output "$env:RUNNER_TEMP/guided-frozen"
```

The fixture covers 33 synthetic states at 1180×820, 920×749 and 680×680, plus the 680×680 text-scale case. It verifies stage/control isolation, resolved font/color/panel values, unclipped bounds, contact scrolling/action dock, keyboard traversal, full error access and timer cleanup. Source and the separate frozen harness must have identical production module code custody, geometry and styles. The harness is not shipped and adds no product test hooks. Real helper and account transport are replaced only at fixture boundaries; installed product, physical DPI and phone/account acceptance remain separate gates.

The Windows workflow prepares an advertised disposable-runner desktop mode when necessary and restores it. Capture fails on clipped, offscreen, occluded or inconsistent client/native bounds. Idle/event-drain measurements use the production pump; the 20Hz full-render comparator is explicitly synthetic, not a historical customer performance measurement.

### Visible desktop qualification

Windows run `39680842276`, source `cd29cdcf2c0ad802683c89e7fd7a986ea73c7548`, passed 113 tests and the logical geometry/style checks for 65 screenshots. Visual review found the taskbar covering the bottom action row in the 920×749 captures. Those logical bounds did not establish visible footer acceptance. The 680×680 captures were clean. That established a harness defect: failure to compare the window with the desktop work area. The later independent normal-opening probe below also established a product sizing defect.

The existing renderer now has a normal-open probe. Run it on the original disposable desktop **before changing resolution or forcing a fixture size**:

```powershell
python source/collector/windows/tests/render_contact_workspace.py --source source --logo source/public/brand/amplifai-by-nexus-original.png --output "$env:RUNNER_TEMP/AmplifaiParityRender/normal_open" --normal-open-only
```

`normal-open-receipt.json` records the actual `PhoneWindow` geometry requests, physical client bounds, native window and DWM visible-frame bounds, monitor/work area, DPI context and overlapping windows. `windows-normal-open-unqualified.png` is always retained as diagnostic evidence, even when the gate fails; its receipt explicitly says `visibilityQualified: false`. A passing probe also captures Contacts and verifies its primary actions at the same normal opening size. It does not resize the window or change desktop resolution. A default client or visible frame outside the work area requires a bounded product placement/size repair and another normal-open check; increasing the fixture desktop alone cannot satisfy that gate. Other probe failures require their own cause analysis before changing product layout.

The shared capture helper now rejects client or visible-frame bounds outside `GetMonitorInfoW`'s work area, overlapping higher windows, Tk/physical-coordinate disagreement and changing bounds during capture. Every accepted screenshot records those measurements. Owned fixture windows are centered using their measured native frame without shrinking the required client size. The tests use a per-monitor-aware capture thread before creating Tk; this is a measurement context, not a product DPI change or physical-DPI acceptance. See [Microsoft's monitor/work-area contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-monitorinfo) and [visible-frame versus virtualized window bounds](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect).

For the forced viewport matrix, the existing locked `pywin32` supplies display-mode APIs. The following helper is restricted to the disposable Windows Actions environment. It keeps an already sufficient desktop; otherwise it chooses an advertised compatible mode of at least 1280×1024, tests it with `CDS_TEST`, applies it with flags `0`, and verifies a work area of at least 1000×850. The original mode is persisted before mutation and restored on preparation failure. The parent workflow must restore it after rendering as well:

```powershell
$desktop = "$env:RUNNER_TEMP/AmplifaiParityRender/desktop-mode.json"
try {
  python source/collector/windows/tests/visible_desktop.py --prepare $desktop
  if ($LASTEXITCODE -ne 0) { throw 'Visible desktop preparation failed' }
  # Run the existing render fixture commands here; check every exit code.
} finally {
  if (Test-Path $desktop) {
    python source/collector/windows/tests/visible_desktop.py --restore $desktop
    if ($LASTEXITCODE -ne 0) { throw 'Desktop mode restoration failed' }
  }
}
```

No registry persistence, taskbar changes, unsafe display modes or offscreen rendering is used. Unsupported modes or insufficient work area fail qualification. The API behavior follows [ChangeDisplaySettingsEx](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changedisplaysettingsexw) and the existing [pywin32 binding](https://mhammond.github.io/pywin32/win32api__ChangeDisplaySettingsEx_meth.html). Twelve headless regression tests reject the taskbar-overlap case, clipped frames, DPI mismatch and occlusion, and check exact placement, tested modes and restoration. The harness-only repair was committed as `99ee89d`; corrected visible-desktop matrix results remain pending.

### Normal startup fit

The unforced Windows probe at source `0cfa9c4`, run `39683411577`, measured client `[86,31,1006,780]`, visible frame `[85,0,1007,781]` and work area `[0,0,1024,720]`. Artifact `12385651228` (ZIP SHA-256 `eb718736f9007d55f8a4f88ec1c414dad2fbc89e273c1a7e5cb3cd19781d30e6`) retains the receipt and taskbar-obscured screenshot. The normal product window exceeded the work area before any fixture resize or display preparation.

`PhoneWindow` now fits its startup window after layout using the current monitor's measured work area, native client size and DWM visible frame. It reduces only excess dimensions and moves only as far as needed. The existing 680×680 logical minimum and all typography/styles remain unchanged. `SetWindowPos` preserves activation and Z order; its physical-coordinate API scope restores the thread's prior DPI context. The product does not import fixture helpers or alter screen resolution. See [window placement flags](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos) and [DPI context restoration](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext).

Eight headless regressions cover the measured failure (calculated client 920×688 and visible frame ending at work-area bottom 720), sufficient desktops, negative monitor origins, top/left taskbars, offscreen placement, minimum size preservation, DPI context restoration on failure and scaled coordinate units. A work area smaller than the decorated minimum remains unsupported: the minimum is retained and the title is placed within the work area, without claiming every control fits. Dynamic monitor/DPI changes after startup and physical DPI acceptance remain separate limits. Actual Windows normal-opening and full matrix requalification of this product repair are pending; the normal-open fixture still does not force a viewport or resolution.

## Package shape and qualification

The build_windows_candidate.py script freezes distinct console helper and Tk window runtimes into a disposable onedir candidate. The separate build_windows_msi.py script authors an unsigned WiX 3 per-user MSI only after the frozen package's exact file hash inventory and legal files match:

    AmplifaiPhone/
      AmplifaiPhone.exe
      _internal/                         Tk and wrapper runtime
      resources/amplifai-by-nexus-original.png
      resources/phone-helper/
        AmplifaiPhoneHelper.exe
        _internal/                       complete separate helper runtime

The wrapper accepts only these contained regular resources, with no linked runtime or PATH/source fallback. It launches the fixed helper with private stdin/stdout, discarded stderr, its own directory, and a copied environment without PYTHONPATH or PYTHONHOME. The original logo bytes are unchanged. PACKAGE-INPUTS.json records the input shape. The Windows x64 Python 3.12.10 dependencies are hash-pinned in collector/windows-requirements.lock.

The disposable public Windows runner uses the source-only codex/windows-x64-candidate-oct8 branch. On run 37862692679, the old Python temporary fixture failed ancestor index 6 because its owner did not match the effective reduced user. A fresh fixture created under the reduced token with the app's protected owner/DACL passed while every ancestor remained pinned; the candidate restored ancestor FILE_READ_ATTRIBUTES (0x80), root READ_CONTROL and attribute readback, no-share-delete, and no-follow reparse checks. Earlier run 37863879590 passed 38 wrapper tests, 23 storage checks with the reduced-token probe, and 201 engine tests, then opened/closed the frozen GUI. The rc12-compatible source at `47776e1bf149` produced a private unsigned MSI on Windows2025 runner 37975221058 with WiX 3.14.1.8722. Its exact 7,289-file / 104,079,534-byte inventory, MSI SHA-256 `71d0c59fd3649f20b98a640b5e70ae61c2430c57ef249d0e6840f1e90a9820ac`, runner-user install/uninstall, and private draft tag `windows-rc12-private-47776e1bf149` are recorded in the canonical project ledger. The draft is not a customer release.

The closed rc12 wrapper had 38 Windows synthetic/headless tests, including 25,001 selected contacts across 200-contact previews and 1,000-ID review commands, stale-response rejection, and final saved-ACK gating. The distinct private rc13 candidate at `43eb26bd342a8f9b14a2f2c3ff03e2ba3137ddd5` passed standard Windows2025/WiX 3.14 runner `37997875834`, including synthetic Tk controls, frozen helper, and runner-user MSI install/uninstall. Its **unsigned private draft** MSI is 41,001,852 bytes, SHA-256 `271655ac6a79780af108c6b6115b9beea210970f42ecd155e1975c022a7bbaea`. Independent 680 px render review found the four-button consent row clipped the Full privacy guarantee label. The rc14 private candidate wrapped those actions into two existing-style rows and passed actual 920/680 px synthetic Tk checks and runner-user install/uninstall. Its unsigned MSI remains private. The distinct rc15 private candidate bound the newer shared extraction modules and verified the frozen encrypted-size worker; the current ACK-safe wrapper still requires its own private runner/package qualification. None of these checks establishes physical iPhone or authenticated account acceptance.

Before any later TEST update: qualify the exact frozen helper, MSI, actual Windows Tk guide/details, runner-user install/uninstall, corresponding source, and public bytes. A Windows publisher certificate is unavailable under the no-new-spend constraint, so label any subsequent public package unsigned TEST. The existing Ed25519 update signature verifies distribution bytes; it is separate from Windows publisher signing. Apple Devices/USB, normal non-admin/SmartScreen policy, high-DPI accessibility, physical iPhone Trust/password, and authenticated account Save/ACK/report remain real-PC acceptance gates; never instruct users to bypass security policy.

## Source diagnostics

Current source failures are validated with the shared strict v1 contract before entering the Windows state. The existing coverage label shows the current unavailable reason and byte context before account Save. The source-details disclosure uses the existing read-only text, button, scrollbar and Escape/Close patterns. Readable zero remains zero.

Previous-run diagnostic history is labeled separately and never enters a new capture or account Save. The helper retains at most ten attempts for thirty days in a private marker-owned diagnostics directory beside the unchanged sessions directory, bounded to 64 KiB. It contains only allowlisted counters and validation states, never source rows, private paths, credentials or raw digests. A fixed storage-failure state remains visible when local retention fails.
