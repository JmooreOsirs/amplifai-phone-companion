# One approval at the start: AMPLIFai Phone Mac handoff for Jeff (2026-10-19)

## Approved October 19 native UI integration

Jeff approved the reconciled Fable/Brian implementation and exact revised consent wording, with Astra / Ultra for implementation. This section records the new UI delta; the original PR proposal below remains historical and is superseded wherever it conflicts with the canonical October 19 packet or later integration ledger. Publication still requires the coordinated protocol, migration, cross-version and signed-device gates.

### Reference contract and primitive mapping

Authority: RC28 native source `8f7ec4d`, accepted 1180×820 geometry and Round 5 reference captures, plus the approved October 19 requirements P03–P12/P15. `Brand.swift` is unchanged: Open Sans body, Arial bold headings, existing navy/lime/blue palette, 12/20/28 spacing, 8/10/12 radii. No second design system or downloaded illustration assets.

| Changed element | Existing primitive / deliberate derivation |
|---|---|
| Connect → Unlock → Trust guide | Existing phone/Mac SF Symbols and blue illustration treatment; `brandControls`, body 14, heading 16 and existing spacing. Instruction numbers never masquerade as detected device states. |
| Finder encryption guide | Existing four-step guide extracted into `FinderEncryptionGuide`; `PanelStyle`, control surface, `VerifiedStatusRow`, Lime/Secondary/Quiet buttons. Static Finder illustrations name the real OS control and are explicitly labelled illustrations. |
| Encryption-off decision | Existing read-only model check; `VerifiedStatusRow` plus one primary Finder guide, secondary **Continue with available data**, quiet **Recheck**. Recheck does not start collection. No in-app setting/password change. |
| Approval/account preparation | Existing verified-account row and model-driven journey stepper; repeated legal fact cards removed. The already disclosed account, course and category authority remain visible; `Privacy details` opens the existing Account Privacy Notice. |
| Local review | Existing 220 pt adaptive cards, inclusion checkboxes, separate move flags, raised cards, recessed well and sticky Continue. Quiet Organize label shortened. Calls/Messages unavailable stay visible; source diagnostics remain accessible on demand. |
| Browser handoff / recovery | Existing panels and metric cards. Selected local contacts/calls/messages are labelled as such; only approved categories appear. Exact verification retry and final-receipt retry use model APIs without new consent or a new save. Waiting is not shown as confirmed saving. |
| Collection activity / completion | Existing status panel, timing and byte counts. **Stop collection** remains a local cancellation action. Waiting/unmeasured/stale activity has static blue dots; progress animation retains reduced-motion guards. Native **Close** does not duplicate the website's report-bound Done. |

Apple instruction evidence, checked October 19: [encrypted backups](https://support.apple.com/108353), [Finder sidebar](https://support.apple.com/guide/mac-help/mchl83c9e8b8/mac), [device connection](https://support.apple.com/108643). Finder's installed English `LocalizableMerged.strings` also resolves `SD17` to **CDs, DVDs, and iOS Devices**. Enabling encryption is the owner's Finder action and can overwrite earlier local backups; encryption never promises complete call history.

### Qualification boundary

Offscreen `NSHostingView` renders exercise production SwiftUI views with synthetic contacts and a simulated run authority. No window is created, app activated, helper/device process started, customer files read or account action performed. The render receipt resolves native fonts, colors, spacing and radii; existing brand tokens are unchanged. Changed state previews cover the accepted 1180×820 plus 375×844, 768×900, 1440×1000 and short 820×640 / 1180×680 sizes. Narrow layouts retain outer scrolling and the contact action dock.

Verification: release Swift build passed. Saved PNG/JSON evidence is in the canonical October 19 packet at `native-ui-renders/`; `integration-evidence.json` records source hashes, pass provenance, the accepted 1180×820 reference comparison and unverified boundaries. Preparation, contact/review and final status renders are retained separately so an earlier unchanged area is not misrepresented as a new full pass. Runtime fonts resolve to OpenSans-Regular / Arial-BoldMT and the existing sRGB/geometry tokens. No fixture executable or app bundle is included in that evidence folder. Privacy details pins `phone-run-v1` to `account-2026-10-v6`, and the new v2 disclosure to the agreed v7 Phone section.

Physical/signed qualification remains separate: Finder guide followed on a real phone; read-only recheck; encrypted/off/unknown availability; keyboard and VoiceOver interaction; system reduced-motion setting; real browser focus and final saved receipt. UI does not claim local Disconnect revokes account authority. Coordinated server Stop and retained-run expiry/replacement handling belong to the protocol integration and cannot be proven by these previews.


Brian's correction after his real test:

> "I only wanted to do it at the beginning … it switched back to my app in the middle of this process … If I have to have permissions it shouldn't switch back to the app."

This branch makes the Mac half of that flow: **one informed approval in the signed-in account at the start**, then collect, choose contacts, and the website saves, reports and finishes on its own. The Mac shows no destination sheet and no consent checkbox, and never pulls focus back after the handoff.

| Item | Value |
|---|---|
| Base | `release/native` at `8f7ec4d` (RC28 source) |
| Branch | `codex/brian-simple-mac-flow-oct19` on `Osirs-io/amplifai-phone-companion`. PR #10 targets `JmooreOsirs/amplifai-phone-companion` `release/native` |
| Linked website change (private) | `JmooreOsirs/amplifai-database-engine#5`, head `c34a420`. It defines the server contract (migration `0058`, written and not applied) |
| Helper (Python) | **Unchanged.** One new test file only (`source/collector/tests/test_bridge_run_timing.py`) |

## The flow

| Step | What happens |
|---|---|
| Agree (website) | **Agree and collect** records one run approval: account, course, contacts, call history and message metadata, and automatic saving of the contacts chosen later. **Open AMPLIFai Phone** sends `amplifai-phone://start?run=…&bootstrap=…` |
| Mac opens | The app redeems the one-use start key over its fixed TLS authority, using a run key it generates itself. It shows the verified account and course, **Approved to save: chosen contacts, their call metadata, their message metadata**, and one **Start collection**. Start is an action, not consent, and must happen within 10 minutes of the claim. |
| Start | The app resends its exact original claim right before collection starts. That revalidates revocation, session, course, receipts, retention and reset on the server. iPhone Trust and the backup password are unchanged. |
| Collect, choose | Unchanged progress and contact selection. |
| Continue | The helper pairs. The app binds that one handoff and digest to the run, opens the account page in the browser that sent the link, and minimizes once. From here the website owns the screen. |
| Website connects | The helper reports a pending account check. The app asks the server whether that intent is exactly this run's (account, course, enrollment, handoff, digest). Only an exact match is approved automatically. A mismatch or refusal is declined, and the website then asks once for a replacement approval. An unclear answer is left undecided. |
| Save, report, Done | Website only. The Mac waits for `saved`, sends `finish`, and stays minimized. |

**Never after the handoff:** no app activation, no restore, no raise, no sheet, whether for status, `received`, `saved_pending_browser_receipt`, `saved`, helper exit, completion or errors. The owner can reopen the window from the Dock. It then shows passive status and an explicit **Continue in browser**.

## Changed files

| File | Change |
|---|---|
| `Sources/Services/PhoneRunAuthority.swift` (new) | Fixed-host HTTPS client: ephemeral session, no redirects, 2 KB strict replies. Each request is prepared once and resent byte for byte on an unclear outcome. Includes the injectable protocol. |
| `Sources/Support/PhoneRunLink.swift` (new) | Strict link parser, return-browser choice, and a one-slot inbox that holds a link from cold start until start-up checks finish. |
| `Sources/App/AmplifaiPhoneApp.swift` | Apple Event `kAEGetURL` handler, registered before launch finishes. It records the sending browser and keeps SwiftUI from opening a second window. |
| `Info.plist` | `CFBundleURLTypes` for `amplifai-phone` (production app only; the demo declares none). |
| `Sources/Services/CollectorModel.swift` | Run claim and exact retry; Start window and revalidation; a different-link candidate kept on unclear outcomes; bind-once; automatic destination decision after verification; handoff without waiting for `received`; expiry fails closed; local collection approval composed from the verified run (disclosure `native-run-v1`, carrying server run, approval and `phone-run-v1`). |
| `Sources/Support/CompanionWindowDirector.swift`, `AppKitCompanionSurface.swift` (new) | Every focus change goes through one surface. The director only minimizes after the browser actually opened the current navigation. A flow check enforces that only the surface file may call AppKit activation. |
| `Sources/Support/ActiveCompanionWindow.swift` | Destination `NSAlert`, `NSApp.activate`, `makeKeyAndOrderFront` and deminiaturize-on-error removed. The close confirmation stays, for the owner's own click only. |
| `Sources/Support/BrowserReturn.swift` | Opens `https://amplifai.satoris.ai` pages in the sending browser, or the default one. |
| `Sources/Views/ContentView.swift`, `BrandControls.swift` | Start in your AMPLIFai account / approved run card / Start collection; Continue; passive post-handoff status. |
| Tests: `PhoneRunFlowChecks.swift` (new), `CollectorFlowChecks.swift`, `CollectorModelChecks.swift`, `SourceDiagnosticChecks.swift`, runners | See Evidence. |
| Fixtures: `SimulatedPhoneRunServer.swift`, `FakeDataDemo/FakeBridgeTimeline.swift` (new), `FakeHelper.swift`, `FakeDataDemoApp.swift`, `FakeDemoChecks.swift` | The fake helper follows the real `bridge.py` timing: `received` only after `/v1/complete`, a 5-minute window from `pair`, and a 2-hour transfer window refreshed per page. The demo starts with a simulated Agree and collect. |
| `source/script/build_fake_data_demo.py`, `run_fake_data_demo.sh`, `render_macos_review_acceptance.py` | New fixture sources; isolation now also requires `noUrlScheme`. |
| `source/script/stage_local_device_test.sh` | New versioned output `amplifai-real-test.run-approval-v1.<time>`; adds `CFBundleURLTypes` to the staged copy only; still never launches or registers. |

## Timing (each clock is separate)

| Clock | Starts | Length | Defined in |
|---|---|---|---|
| Approval window | Agree and collect | 1 hour to redeem | website migration `0058` |
| Start key | Each issue | 10 min, one use | website migration `0058` |
| Start window (claim replay) | The claim | 10 min | website migration `0058` |
| Run | The claim | 12 hours | website migration `0058` |
| Initial bridge | Helper `pair` after review and **Continue** | 5 min to connect, approve and release | `bridge.py` `PAIR_SECONDS` |
| Connect intent | Website creates it | 5 min to consume | website migration `0025` |
| Active paged transfer | Release | 2 hours of inactivity, refreshed by each page, decline and keepalive (the website sends one every 5 min) | `bridge.py` `TRANSFER_SECONDS` |

## Known limits

- **An expired connection can't be resumed.** The helper reports `expired` before release (no browser connection within 5 minutes of Continue) and after release (2 hours without activity; some sources may already be saved). A new `pair` always creates a new handoff, and the run binds one handoff and digest once, so the run is spent. The app says: "This connection expired. Nothing more can be sent from this collection. Check your account for any sources already saved. Starting again needs a new collection." It never re-pairs, rebinds or marks anything received. Smallest recovery: **Discard review** in the app (a new link is ignored while a review is open), then **Agree and collect** and **Open AMPLIFai Phone** on the website, and collect again.
- **Start within 10 minutes of the claim.** After that, the app shows "approve again" in place of Start. Once collection starts, the run lasts 12 hours, so a normal 35-minute collection still binds.
- **Browser profile and tab.** Account pages reopen in the browser that sent the link, which is read from the Apple Event sender, or else the default browser. macOS can't choose that browser's profile, window or tab. This is untested on a real browser, and the fake website fixture doesn't prove it.
- **Requires the website.** This build has no legacy flow. It needs a website with `0058` installed and the run flag on; without them it offers only **Start in your AMPLIFai account** and can't collect.
- **Older apps.** RC28 and earlier don't handle the `amplifai-phone` link. See the release order.

## Release and rollback order (coordinated: website PR #5 and Mac PR #10)

**The two sides don't fall back to each other:**
- **RC28 and earlier** can't handle the `amplifai-phone` link. They use the legacy flow, which works only while the website flag is **off**.
- **The new Mac build has no legacy flow.** It always needs a website run, so with the flag off it can only offer **Start in your AMPLIFai account**, and nothing can be collected.
- Turning the flag off restores collection for RC28 users only.

**Order:**
1. **Qualify `0058`** on an isolated database (required; its PostgreSQL fixture has never run). Then apply it. Enforcement switches on with the schema, and no runs exist yet.
2. **Deploy the website with `NEXT_PUBLIC_PHONE_RUN_APPROVAL_RELEASE` off.** RC28 keeps the legacy flow.
3. **Build and sign the new Mac build, staged but not in the production feed or download yet.**
4. **Accept it before any broad rollout:** a signed build against a website with the flag on, on an approved official origin (the Mac bridge accepts only `amplifai.satoris.ai` and `amplifai-database-engine.vercel.app`). Run:
   - a full-size collection (about 35 minutes; 5,134 contacts and 302,190 messages);
   - the automatic save, report, Done and Print;
   - a check that the real browser comes forward and the Mac never does;
   - the revoke, replacement and expiry paths.

   **Gap:** the flag is a single build-time switch, and there's no per-account test gate. Turning it on in Production affects every owner, and RC28 owners would get a link they can't open. Jeff decides before this step: a short controlled window, or adding an account-level gate first.
5. **Coordinate availability:** publish the accepted Mac build to the feed and download, confirm existing installs update, then enable the flag.

**Owners still on RC28 after the flag is on:** nothing opens. **Recovery:** update AMPLIFai Phone, then select **Open AMPLIFai Phone** again within the 1-hour approval window. After that window, agree again.

**Rollback:**
- **Website flag off:** stops new runs. Existing runs keep their server-side fences, and RC28 owners get the legacy flow back. Owners already on the new Mac build **can't collect** until the flag is on again, or until a Mac build with the legacy flow is published.
- **Mac rollback:** Sparkle doesn't downgrade, so going back means publishing a new, higher build that carries the earlier behavior. Run-bound handoffs from this build then fail closed.
- **Keep the `0058` tables and functions.** Dropping them removes release history and save-authority links.

**An expired connection, smallest recovery:** in AMPLIFai Phone, choose **Discard review**. A new link is ignored while a review is still open. Then, on the website, **Agree and collect** and **Open AMPLIFai Phone**, and collect again. Anything already saved stays in the account.

## Evidence

**Mac checks** (macOS 26.6.2 arm64, Swift 6.3.3), run from `source/collector/macos`. All exit 0:

```text
swift build --configuration release   → Build complete
sh Tests/check_flow.sh                → Mac source diagnostic … checks passed
                                        macOS flow and byte-progress checks passed
sh Tests/check_model.sh               → 20 macOS recovery, progress, run-approval and update-deferral checks passed
sh Tests/check_contact_provenance.sh  → macOS contact provenance and 672-contact workspace checks passed
sh Tests/check_updater.sh             → Sparkle, updater deferral and status-process lines passed
sh Tests/check_fake_demo.sh           → fake helper links no network, device or process-spawning API;
                                        FAKE DATA demo isolation, real bridge order and clock, selection-driven
                                        sample report, one-approval journey, cancellation and recovery checks passed
```

**What the new `PhoneRunFlowChecks.swift` (inside `check_flow.sh`) covers:**
- link parsing and return-browser choice;
- the one-slot cold-start buffer and idle admission;
- claim success and refusals (forged, replayed, changed, revoked, stale, 404);
- lost-reply and malformed-success exact retry;
- no collection or pairing before a verified claim, and old local approvals never counting;
- Start revalidation: revoked, changed, uncertain or stale answers, and the 10 min 01 s window;
- a duplicate link, and a different link that is refused, uncertain or retried;
- Start at 9 minutes, then a 35-minute collection that still binds;
- bind once, identical retry, different handoff refused, malformed bind retry, replacement approval;
- verify: automatic approval on an exact match; a decline on each mismatch; an uncertain answer left undecided; stale and late destination events ignored;
- handoff after bind without waiting for `received`;
- zero activate, restore, raise or sheet calls across pending, approved, received, saved_pending, saved, helper exit, completion and errors;
- a late browser-open answer;
- a 15-minute released transfer;
- expiry before and after release;
- the "Approved to save" label and the production request shape;
- a source scan that allows AppKit activation calls only in `AppKitCompanionSurface.swift`.

**Mutation checks:** putting back the restore-on-error, or making the handoff wait for `received` again, each makes `check_flow.sh` fail. Both edits were reverted, and the files are byte-identical.

**Real helper timing.** Run from `source/collector` with the existing `/Users/briancurtis/.local/bin/python3.13` (3.13.12; nothing installed) and `PYTHONPATH=.:tests`:
- `-m unittest test_bridge_run_timing` (new): `Ran 3 tests … OK`. It drives the unchanged `bridge.py` with an injected clock, a synthetic paged capture and port 0. Release at the edge of the 5-minute window works, and a later one is refused. Pages read over 15 minutes after release still succeed, then complete and acknowledge. Two hours of inactivity expires.
- `-m unittest test_bridge test_bridge_ack` (existing): `Ran 25 tests … OK`.

**Independent clock evidence** (parent workspace, read-only, inert I/O): `verify_paged_expiry_clock.py` and `paged-expiry-clock-results.json` run the real `bridge.py` methods. Observed:
- after a 35-minute capture, `pair` starts a fresh 300 s window;
- release at 299 s sets a 7,200 s transfer deadline;
- release at 301 s returns 410, with no proof call and no token;
- 3,074 page-handler calls over 35 synthetic minutes reach `received`, with zero further proof or approval calls (counts stand for 5,134 contacts and 302,190 messages; no real rows were loaded);
- keepalive renews, and exactly 2 hours of inactivity returns 410.

**Release control**, with the same Python 3.13.12:
- `python3.13 -W error -m unittest discover -s release_control -p 'test_*.py'` → `Ran 32 tests … OK`
- `PYTHONPATH=source/script python3.13 -W error -m unittest test_release_source_offer` → `Ran 9 tests … OK`

The system Python 3.9.6 can't run them (`hashlib.file_digest` needs 3.11+).

**Not run or not proven:**
- The FAKE DATA demo bundle, the render fixture and the staging script were typechecked or edited only. No bundle was built, and nothing was launched.
- `release_control/producer.py` doesn't run `check_model.sh`, `check_fake_demo.sh` or the new Python test; add them if you want them in CI.
- **Rollout blockers:**
  - the website's `0058` SQL is UNEXECUTED, so the database needs qualifying;
  - a real end-to-end run at Brian's scale (5,134 contacts, 302,190 messages) has never been done, so real serialization, throughput and database reliability are unproven;
  - real URLSession against the server, the real website, and on-screen focus behavior are unverified.
- Timing is qualified only by the synthetic checks above.

## Steps for Jeff

1. Review this PR together with website PR #5. Note that the destination sheet and the local consent checkbox are gone by design.
2. Qualify `0058` and roll out in the order above. Signed-build acceptance comes before any feed or flag change.
3. Choose the next identity (build above `26101628`), build ARM and Intel with the protected workflows, approve `approvedSources`, then sign, notarize and publish. Update the feeds, `releases.json` and the website's pinned build numbers as for RC28.
4. Real-device acceptance on the signed build, with the website flag on in a test environment:
   - Agree and collect → Open AMPLIFai Phone → Start → Trust and password → a 35-minute collection is fine → choose → Continue → the browser comes forward → automatic save of a large selection → report → Done and Print.
   - Confirm the Mac never comes forward.
   - Exercise the expired, revoked and replacement paths.
5. Windows is unaffected.

Nothing was signed, published, deployed, merged or migrated. No URL scheme was registered and no staged build was launched. The installed RC28 app was never modified or stopped.
