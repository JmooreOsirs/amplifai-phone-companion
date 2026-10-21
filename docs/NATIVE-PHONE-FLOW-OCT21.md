# Native Phone flow — October 21 implementation contract

## Work agreement

- Outcome: deliver the approved native preparation, native consent, contact workspace, progress and original-browser return changes, then qualify and publish the next native release with the website owner.
- Baseline: RC33 native `f9181b8509af81688dd457e8f93ddf3ee3d63b38`; working branch `codex/native-phone-flow-oct21` in the existing native checkout.
- Setting: previous implementation/release setting GPT-6.1 Sol / High; this batch GPT-6 Astra / Max, applied by the coordinator's `send_message_to_thread` model/thinking override. Jeff's explicit top-model/Max direction is visible in coordinator turn `01a15f87-5195-76a3-a13f-9d099c049e0f`. This records the accepted task control, not an inferred subscription cost. Return milestone: qualified implementation and release closeout; no automatic restoration claimed.
- Finite milestone: agreed cross-app protocol, written reference/impact contract, runnable native UI candidate and matched-window verification. Independent UI work proceeds while the server contract is reconciled.
- Scope: native repository only. Website owner owns server/API/SQL and publication of website feeds. Coordinator owns media reconciliation and shared decisions. No repeat media analysis, installed-app changes, private capture, customer data/consent action or encryption-setting change.

## Reference and impact contract

Authority is the current RC33 source/rendered native design and approved Phone inventory B04/B06–B17. This implementation consumes the existing assisted-review inventory; it is not a fresh audiovisual rewatch.

- Typography: retain `Brand.bodyFont` (bundled Open Sans, existing Arial fallback) and Arial bold headings. Existing action/headline hierarchy is 14/16/18/22/28; increase emphasis using that grammar.
- Palette: existing `Brand` background #0f1525, panel #141d30, border #263146, heading #edeff3, body #c8cfd9, muted #a1a9b5, lime #a6e63d, blue #5c87df.
- Spacing/radii/depth: retain `Brand.Space` 12/20/28, `Brand.Radius` 5/8/10/12, raised gradient and existing well/card shadows. Controls remain LimeButton, SecondaryButton and QuietAction; instructions/help use existing panels and disclosures.
- Layout: preserve workspace maximum1440 and minimum contact card220. Existing1180×820 default is not the missing implementation. Verify restored geometry and clamp useful opening geometry to actual visible display bounds. Contact review keeps fixed context/actions and gives available height to its inner scroll region.
- Motion: enlarge existing transport/local-reading primitives; honor reduced motion. Stage labels reflect actual model events. No percentage, ready, Saved or completion claim without its corresponding event/receipt.
- Map: preparation/help → PhonePreparationGuide + existing DisclosureGroup/panel; handoff prominence → reviewPanel/LimeButton; original-browser activation → BrowserReturn; stable contact workspace → ContentView/ContactReviewView; opening geometry → existing scene/window bridge; native approval → explicit pending-setup model/service state after server contract agreement.
- Preserve transport/page manifests/digests, full-size transfer, retry/refresh, source diagnostics, exact contact choice/grouping, legacy source-specific Save, final account receipts, retention and cleanup. Unsupported SMS size growth remains rejected.
- Verification: source token comparison plus matched synthetic native windows at1180×820, narrow768 and375, and recorded restored-small/limited-display cases. Existing fixture must intercept browser/network/device actions. Responsive menus use native macOS menu semantics with Brand body type and the existing lime action color; detail popovers reuse panel/well tokens. A synthetic rendering is not physical acceptance. No installed app is launched.

## Proposed native-consent protocol — awaiting server-owner agreement

This section is a proposed wire contract, not an implemented API. Current `phone-run-v2` already authorizes automatic saving of subsequently selected records, but its approval is recorded on the website. Existing claim/preparation/start endpoints cannot record native consent.

1. **Create pending setup in the original signed-in tab.** Website authenticates owner/session, enrollment/course and current policy/retention generations. A bounded, short-lived pending setup stores those bindings and categories; it grants no collection or Save. The tab remains the waiting/report surface. Open installed app sends a new strict setup deep link (proposed `amplifai-phone://setup?setup=<uuid>&bootstrap=<43 base64url>`). A plain DMG/app launch contains no account identity and must explain the original-tab Open app action.
2. **Claim setup once.** Native generates a distinct32-byte setup capability before sending the exact bootstrap claim to the fixed TLS authority. Server atomically consumes the bootstrap and binds that capability; exact same-request retry/status recovery is idempotent, other replays fail. Response supplies verified owner display/course/enrollment, setup ID/expiry, canonical categories, versioned native disclosure and policy/retention references. No collection run or preparation grant is returned yet. Capability stays private in memory; no browser cookies or arbitrary authority URLs.
3. **Show and deliberately approve.** Native presents the verified destination and exact versioned collection-and-selected-data-save disclosure, with policy/help access. One deliberate approval action sends setup ID/capability, immutable approval request ID, the displayed disclosure version/digest and a separately generated run capability. A new native-location disclosure version is required; never relabel old v1/v2 receipts. Server rechecks owner/session/course/policy/retention/revocation/expiry and atomically writes the approval plus the prepared run. Repeated identical request returns the same receipt/run; changed destination/disclosure/categories/request payload is rejected.
4. **Resume existing preparation only after confirmed approval.** Success returns a strict receipt containing run/approval/enrollment identity, canonical categories/disclosure and bounded expiry, plus existing preparation status. Native matches every binding before storing a usable run. An uncertain/malformed success retains only the exact pending request; retry/status may recover it, but collection stays disabled. Cancel/expiry/refusal grants nothing. Restarted processes cannot guess/recover capability from URLs or logs.
5. **Existing operation/selection/save semantics continue.** Native Connect/Unlock/Continue/Trust and Start use current device/operation admission. After exact contact choice, existing immutable handoff/digest and receipt protocol applies. Combined consent permits automatic Save only for that approved selection; legacy paths retain explicit source Save. No early cleanup.

Server owner to finalize endpoint names, canonical response fields/caps, receipt/disclosure bytes and exact migration. Native implementation will use agreed strict parsing and contract fixtures, with wrong owner/course, stale/replayed bootstrap, expired/revoked generation, double approval, uncertain reply and legacy compatibility checks. Existing authority transport limits and redirect/cookie separation remain enforced. A setup capability proves possession of the authenticated bootstrap; it is not a claim of hardware or signed-binary attestation.

## Browser return contract

Primary final return activates the existing originating browser application and opens no URL. Copy directs the person to the existing AMPLIFai account tab; macOS cannot guarantee exact tab/profile selection. Missing/closed-tab recovery is a separate explicit Open account action. Completed-return recovery uses only the existing bound report URL; it cannot re-pair, recollect or Save. Keep local-ready, browser-received, save-reported and final Saved receipt states distinct.

## Current progress

- Primary browser return uses the captured running HTTPS handler and activates it without a URL. Missing/closed browser fails without default-browser navigation. Explicit recovery retains the existing bound URL. Added injected-boundary checks; no real browser was activated.
- Default geometry remains 1180×820; the existing window adapter now enlarges an undersized restored frame once per window and clamps it to the visible display. Later user resizing is preserved. Pure geometry, a real never-shown window sizing/reattachment check and 40 offscreen AppKit lifecycle cycles pass. The sizing check confirms an actual later resize survives reattachment; an installed cold-launch restoration has not been observed.
- Review now uses a fixed shell and the available height for its contact grid. Responsive selection/group actions retain exact model semantics. The first matched renders exposed footer overflow at 375×844 and a cramped grid at 820×640; the responsive footer/controls were corrected. The final matrix passed 14 states across six viewports and 30 scroll checks; the smallest tested contact viewport is 70 points tall.
- Added actual offscreen scroll movement checks: one contact scroll region, viewport inside the host, usable viewport height and fixed viewport during scrolling. This creates no window and activates no application.
- Setup instructions now initiate Check before any expected Trust prompt. Backup-password help uses existing branded wells/disclosures and distinguishes the encrypted computer-backup password. Apple references verified October21: https://support.apple.com/108353 and https://support.apple.com/108313. No password recovery or device-setting operation is added.
- Progress uses existing colors, wells and symbols with larger transport and local-reading indicators. Actual processing/receipt state governs labels; repeated waiting copy removed.
- First release Swift build passed. Existing source-diagnostic, flow, preparation wire/admission/recovery suites passed. The final UI matrix and release Swift build passed. Later targeted cleanup removes duplicate password prompt copy and labels prior-source help; its two affected states passed targeted rendering at all six sizes, with six renewed history scroll checks.
- Server agreement and native approval implementation remain pending. RC33 cannot parse a setup link. Website must retain legacy web-approved flow and negotiate capability/version, enabling new setup links only after published signed bytes support them. Capability is UX routing, not binary or hardware attestation.
- The late Brian model reply belongs to the completed Oct20 reset. Coordinator confirmed it is stale; no repeated residual check, reset or model rerouting occurred.
- No release identity is reserved, signed bytes changed, installed app launched or customer/phone action performed in this batch.

### Candidate render evidence

- Baseline: `/private/tmp/amplifai-mac-visual.oct21-phone-before`.
- Intermediate candidates: `/private/tmp/amplifai-mac-visual.oct21-phone-ui-v1`, `...-v2`, `...-v3`; failures are retained as bounded fixture evidence and are not release acceptance.
- Scroll probe: `/private/tmp/amplifai-mac-visual.oct21-phone-scroll-probe`; detected the 820×640 viewport-height issue.
- Final matrix: `/private/tmp/amplifai-mac-visual.oct21-phone-ui-final` — 14 states, six viewports, 30 actual scroll checks, 97 PNGs. Native style-token readback matches baseline exactly.
- Targeted final cleanup: `/private/tmp/amplifai-mac-visual.oct21-phone-ui-cleanup` — history and password states, all six viewports passed. The password prompt retains Stop without the duplicate status panel; prior-source help has a visible label.
- Coordinator visual consistency spot-check of v5: `/Users/jeffmoore/Documents/🗣️ Voice Orchestrators/outputs/google-segment-two/new-phone-process-2026-10-21/NATIVE-CONSISTENCY-SPOT-CHECK.json` confirms the approved native grammar and visible primary Continue at 1180×820 and 820×640. This is visual evidence, not native-consent or customer acceptance.

## UI milestone limits and next action

The UI candidate is a source milestone, not a published release. Ordinary real-browser focus/tab return, physical iPhone transfer, final account Save/report and installed cold-launch behavior are unobserved in this batch. Native controls and menu semantics are preserved; a live keyboard navigation session was not run. No real device, private capture, customer account or browser was operated.

Next: implement the exact server-approved pending-setup and native consent protocol, then rerun its changed gates, preserve legacy version negotiation, qualify both release architectures, and coordinate signed publication/feed deployment with the sole website owner. No native consent is inferred from the existing web approval.
