# Simpler Mac phone flow — handoff for Jeff (2026-10-19)

Brian asked for a much simpler AMPLIFai Phone Mac flow: agree → collect with clear progress → choose all or
some contacts → get the existing report. This branch does that with minimal changes to the RC28 native source.
It also adds a clickable FAKE DATA demo so the flow can be tried in seconds without an iPhone or an account.

| Item | Value |
|---|---|
| Base | `release/native` at `8f7ec4d` (the RC28 source; both RC28 release tags point here) |
| Branch | `codex/brian-simple-mac-flow-oct19` on `Osirs-io/amplifai-phone-companion` (fork of `JmooreOsirs/amplifai-phone-companion`) |
| PR target | `JmooreOsirs/amplifai-phone-companion` `release/native` |
| Scope | Mac app views and model text, Mac tests, fake-data demo and scripts. No helper (Python), Windows, website, release-control, version or signing change. |

## Verified baseline (read-only, 2026-10-19)

- GitHub Latest is `macos-2026.10.16-rc28-release` (Intel test: `macos-2026.10.16-rc28-intel1-test`), both at `8f7ec4d`.
  The release receipt names source `8f7ec4d`, build `26101628`, Developer ID `WSBR54954W`, app and DMG notarization Accepted.
- The installed `/Applications/AMPLIFai Phone.app` is `2026.10.16-rc28` / `26101628`, arm64, notarized. All 5,733 files
  match the release's `final-signed-app-inventory.json`.
- Production website feed (both hosts) is byte-identical to the release `appcast.xml` (`22c677a1…`).
- No later commit on any branch changes the Mac app source.

## Flow: before and after

| Step | RC28 (`8f7ec4d`) | This branch |
|---|---|---|
| Agree | Three screens: "Prepare this iPhone" (Check iPhone setting, then a call-history button) → "Approve collection" (checkbox, Agree to collection) → "Ready to connect" (Connect iPhone) | One screen, **Collect phone data on this Mac**. A plain summary comes first (what arrives on the Mac, what stays local, exactly what is shared and when, failure diagnostics). Then **Check iPhone**. After the read-only check: one checkbox, "I agree to this one-time collection and the safe failure diagnostic", and **Start collection**, which records the one-run agreement and starts collecting. |
| Call-history choice | Separate button ("I have the backup password · continue" / "Continue with available data") | Derived from the check: On → encrypted call coverage (password asked later, as before); Off or unreadable → available data. Unreadable keeps **Check iPhone again** as the main action; "Start without confirmed call history" is secondary. |
| Finder call-history setup | A step on the setup screen | Optional link, shown only when encryption is Off |
| Collect | Progress panel; stages Connect / Receive / Read / Review & save | Same panel; stages Connect iPhone / Receive backup / Read locally / Clean up |
| Choose | "Choose contacts"; Include all; All/Main/Review; Select all N / Deselect all; "Mark for group move" uses the same checkbox as inclusion; "Next · N selected" → share dialog | "Choose contacts to share"; scoped buttons name their scope ("Select all 5,129 in Main", "Deselect all in Main"), hidden for All with no search, where they would duplicate Include all; move marks are a flag tag, not a checkbox; footer **Continue to account** ("You will confirm the account before sharing. Save is approved separately in your account.") |
| Share & report | "Local review" panel under the locked contact grid; pairing, code and expiry always visible; a green "Review destination approval" competed with the auto-opened destination sheet; after the save the window closed itself unexplained; stale "Save and continue" recovery text | **Share with your account**, then a one-line summary (contacts, calls, messages) and **one next action per state**: Continue to account → the auto-opened sheet is the **only** destination decision (passive text behind it; a quiet "Review account…" only after Decide later) → "Confirming your sharing choice…" → "Sending to your browser…" → **Finish in your browser** with **Continue in browser** (brings the browser forward, no new tab, so the preview tab is kept; it cannot pick the tab) → "Saving in your account…" → **Saved in your account** with primary **Done** (the window no longer closes itself). Counts, dates, caveats, what is shared, Change selected contacts, Discard review and link/code help sit under one **Details and options** disclosure. |
| Journey | — | "Agree · Collect · Choose · Share & report" bar. Finished steps show a check-circle, never a checkbox, so verified progress is never confused with a choice. |

Gates kept exactly: Apple's Trust prompt and the existing backup password; the per-run local approval (bound to
the run ID, disclosure and scopes, consumed at launch; nothing authorizes a later collection); the Mac
**Share with this account** destination confirmation (account email and course from the authenticated server);
browser Connect; per-source Save in the account. The only approval removed is the preparatory share dialog: it
only prepared the local review and opened the browser, and nothing is transmitted before the destination confirmation.

## Changed files

| File | Change |
|---|---|
| `Sources/Views/ContentView.swift` | One-screen Agree, share panel layout, footer, journey bar, account navigation through SwiftUI `openURL` (same default browser open; lets a host intercept it) |
| `Sources/Services/CollectorModel.swift` | `agreeAndStartCollection`, `settingCallHistoryPreparation`, `journeyStep`, `accountLinkPending`. `submittedDestinationDecision` blocks a second Share or Not-this-account for the same request until the helper answers; it clears on that answer, on a link reset and on `handoff_unconfirmed`. The owner's Share is also recorded for that exact handoff and intent, so a later matching receipt reconciles a lost approval (never counted as a save), and the page says "Sharing has not been confirmed" meanwhile. Disclosure `native-local-collection-v5`; recovery text names current actions (no "Save and continue") |
| `Sources/Views/BrandControls.swift` | `JourneySteps`, `VerifiedStatusRow`, `MoveMarkToggle` |
| `Sources/Views/ContactReviewView.swift` | Scoped bulk labels; redundant All actions hidden; flag-style move marks |
| `Sources/Views/CollectionStatusPanel.swift` | Stage names, recovery text, Discard review…, saved-report link |
| `Sources/Support/ActiveCompanionWindow.swift` | Destination sheet: "Share your reviewed contacts with this account?" · **Share with this account** / Not this account / Decide later. Reports when it is on screen so the page never offers a competing decision. **Behavior change for review:** the window no longer closes itself after a confirmed save; it shows Saved in your account until Done or the close button |
| `Tests/CollectorFlowChecks.swift` | Start gates, journey steps, all/subset/none selection, share → confirm → save receipt → completion, cancellation, revoked-link and failed-link recovery. Destination-decision cases: no double decision (Share or decline), `handoff_unconfirmed`, a lost approval reconciled by a matching receipt (including after `handoff_unconfirmed`), and no approval from a receipt without the owner's Share or for another handoff |
| `Tests/CollectorModelChecks.swift` + new `Tests/check_model.sh` | Cancelling the pre-agreement iPhone check stays on Agree; one Start launches exactly the agreed run and consumes it |
| `Tests/Fixtures/RenderMacReviewAcceptance.swift` | New states; every image stamped "SYNTHETIC FIXTURE" |
| `Tests/Fixtures/FakeDataDemo/*`, `Tests/check_fake_demo.sh`, `source/script/build_fake_data_demo.py`, `source/script/run_fake_data_demo.sh` | Clickable FAKE DATA demo and its checks (not part of the shipped app) |

## Test evidence (macOS 26.6.2 arm64, Swift 6.3.3, 2026-10-19)

Run from `source/collector/macos`:

```text
sh Tests/check_flow.sh                → macOS flow and byte-progress checks passed
sh Tests/check_model.sh               → 20 macOS recovery, progress, approval and update-deferral checks passed
sh Tests/check_contact_provenance.sh  → macOS contact provenance and 672-contact workspace checks passed
sh Tests/check_updater.sh             → all four updater/status lines passed
sh Tests/check_fake_demo.sh           → fake helper links no network, device or process-spawning API;
                                        FAKE DATA demo isolation, selection-driven sample report, journey,
                                        cancellation and recovery checks passed
swift build --configuration release   → Build complete
```

- The same four existing suites passed on unmodified `8f7ec4d` before any edit.
- A mutation check reverted the cancel-check rule: `check_model.sh` failed with "Cancelling the pre-agreement iPhone check must stay on Agree". The fix was then restored.
- **Pre-existing, not caused here:** `source/script/render_macos_update_acceptance.py` already fails on `8f7ec4d`. It requires `Brand.swift` and the end of `ContentView.swift` to equal `cdfc1b9` (RC24), and RC28 changed both. CI does not run it.
- `CollectorModelChecks.swift` had no runner on `8f7ec4d`. The new `check_model.sh` runs it. `release_control/producer.py` on `main` still runs only `check_flow`, `check_updater` and `check_contact_provenance`; add `check_model.sh` and `check_fake_demo.sh` there if you want them in CI.

Synthetic screenshots: `python3 -I source/script/render_macos_review_acceptance.py --output /private/tmp/amplifai-mac-visual.<name> --states setup agree-on agree-off agree-unknown connecting transferring-progress main all-selected all-exceptions no-selection review local-review handoff save-receipt recovery-revoked link-failed completed error cancelling`

The renderer draws offscreen bitmaps of the production views. No window, app activation, helper or account is
involved.

## FAKE DATA demo (clickable, seconds, no phone)

```bash
source/script/run_fake_data_demo.sh
```

The script builds a fresh bundle under `/private/tmp/amplifai-fake-demo.<timestamp>/`, checks isolation, writes
`fake-demo-isolation.json` and opens **AMPLIFai FAKE DATA DEMO.app**. Click through:
1. Check iPhone (simulated), tick the one-run agreement, Start collection (about 6 seconds).
2. Choose contacts: Include all, Main or Review, search, single checkboxes, or none. Continue to account.
3. A **SIMULATED AMPLIFai WEBSITE** window (local fixture, labelled) opens where the real browser would and connects.
4. Answer the real Mac sheet: Share with this account.
5. On the Mac, Continue in browser brings the simulated website forward: **Save and view report**.
6. The simulated website shows the sample report with **Done**; the Mac shows Saved in your account.

**Live click-through (2026-10-19, Codex reviewer, `/private/tmp/amplifai-fake-demo.055137`, `cc404a3` plus the report Done change):**
- Check iPhone → one-run checkbox → Start collection.
- Include all 312 → unchecked one contact → 311 selected → Continue to account.
- Share with this account (fictional `demo.owner@example.invalid`, "FAKE DATA DEMO course") → Continue in browser.
- Save and view report → sample report (311 of 312, 1,498 calls, 4,476 messages, 1 not included) → Done.

The reviewer does not claim exact process-quit behavior after Done. No real device, network or account was involved. The simulated website is a native SwiftUI fixture standing in for the website change below, not the React website or its backend.

What is real: the production `ContentView`, `CollectorModel`, start/check/cancel/recovery code and the
destination sheet. The model launches its helper the normal way; the demo bundle simply carries a fake helper
that speaks the same JSON protocol with 312 fictional contacts.

Isolation, in independent layers:
- Separate identity `ai.satoris.amplifai.fake-data-demo.<random>`, outside `/Applications`, with a yellow FAKE DATA banner and window title.
- Only the fake helper is bundled: no `uvx`, no collector runtime, no update feed, and a non-numeric build, so a diagnostic upload can never be formed.
- Updater disabled; status service not started; every `openURL` (Links and account navigation) intercepted and listed, never opened.
- The fake helper imports no socket, URL, IOKit, MobileDevice, spawn or fork API (`nm -u` / `otool -L`).
- The app re-executes itself under a deny-network sandbox profile, however it is opened, and refuses to run unless the OS reports outbound network denied (`sandbox_check`). The build verifies this from an unsandboxed start.

**Sample report limitation:** the real report ("Your saved phone report") is produced only by the website after
an account Save. The sample is computed on the Mac from the fictional selection. It shows selection-driven counts
and says plainly that no website, account, upload or saved report was involved.

## Linked website change (private repository)

The browser side lives in the private website repository, so this public PR links it and copies none of its code:
**JmooreOsirs/amplifai-database-engine#5** (`codex/brian-native-save-and-report-oct19`, base `main` `7dd7917`). It
lists every source with its own unchecked approval and one **Save and view report**, which saves and then runs the
existing Finalize checks. It then opens the full report, adds **Done — I reviewed these results** there (existing
`results_reviewed` step, with before/after report checks), asks for a course when a native return is ambiguous,
and scopes sign-in recovery. It is client-only, with no API or migration change. Its tests pass except 5
`companion-update-status-ui` cases that fail identically on `main`.

**Real-device boundary:** the Mac bridge accepts only `https://amplifai.satoris.ai` and
`https://amplifai-database-engine.vercel.app`, and account intents are bound to Production. A real iPhone run can
therefore use today's Production website now, but the revised website ending can only be validated on a real
device after that PR is reviewed and released to an approved official origin. No origin check was relaxed.

## Coverage labels

| Covered | How |
|---|---|
| Consent gates, journey steps, selection (all/subset/none), share, confirmation, save receipt, completion, cancellation, recovery | Synthetic model checks + fake-helper end-to-end through the real launch path |
| Views and the clickable journey | Offscreen synthetic renders; FAKE DATA demo clicked end to end by the reviewer (above) |
| Website one-step Save, report navigation, report Done | Synthetic actual-component tests in the private website PR |
| Real iPhone, real backup, real browser/website, real account Save and report | **Not tested.** Needs consent, a signed or locally staged build, and (for the new website ending) the reviewed website release |

## Steps for Jeff

1. Review this PR and the linked website PR together. Note the behavior change: the window stays open after a confirmed save. Merge this PR into `release/native`. CI's `release-control-checks` should pass, since release control is unchanged.
2. Run the five Mac checks above on a Mac, or add `check_model.sh` and `check_fake_demo.sh` to `producer.py`.
3. Choose the next identity, for example `2026.10.xx-rc29` with a build above `26101628` (Intel above `261016281`), in `Info.plist` and the Intel baseline as you did for RC28.
4. Website first (it auto-deploys from `main`): merge the reviewed website PR and accept the new build numbers in companion diagnostics and telemetry, as was done for RC28. The disclosure version string is local to the Mac app; the website does not read it.
5. Build ARM and Intel with the protected source workflows. Then add the merged commit to `approvedSources` in `release_control/policy.json` (Brian must approve that PR), sign, notarize and publish with your existing process. Finally update `public/phone/macos/appcast.xml`, `intel-appcast.xml`, `releases.json` and the pinned tests on the website.
6. Real-device acceptance on the signed build:
   - Check iPhone with USB/Trust, with encryption On and Off; enter the password when On.
   - Cancel during the check and during the transfer.
   - Choose all, a subset and none; check Change selected contacts and Discard review.
   - Continue to account → (auto) connect → Mac sheet "Share with this account" → Continue in browser → Save and view report → report → Done.
   - Confirm that RC26 and RC28 users still update.
7. Windows is unaffected (no Windows file changed).

Nothing was signed, published, deployed or merged. The installed RC28 app was never modified or stopped.
