# AMPLIFai Phone RC29 native handoff (2026-10-19)

## Approved October 19 native UI integration

Jeff approved the reconciled Fable/Brian implementation and exact revised consent wording, with Astra / Ultra for implementation. This handoff records the current approved UI and RC29 integration. The original PR10 proposal is historical source, linked below. Publication still requires the coordinated protocol, migration, cross-version and signed-device gates.

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
| Collection activity / completion | Existing status panel, timing and byte counts. **Stop on this Mac** remains local cancellation; **Stop in account…** opens the authenticated account control without claiming revocation. Waiting/unmeasured/stale activity has static blue dots; progress animation retains reduced-motion guards. Native **Close** does not duplicate the website's report-bound Done. |

Apple instruction evidence, checked October 19: [encrypted backups](https://support.apple.com/108353), [Finder sidebar](https://support.apple.com/guide/mac-help/mchl83c9e8b8/mac), [device connection](https://support.apple.com/108643). Finder's installed English `LocalizableMerged.strings` also resolves `SD17` to **CDs, DVDs, and iOS Devices**. Enabling encryption is the owner's Finder action and can overwrite earlier local backups; encryption never promises complete call history.

### Qualification boundary

Offscreen `NSHostingView` renders exercise production SwiftUI views with synthetic contacts and a simulated run authority. No window is created, app activated, helper/device process started, customer files read or account action performed. The render receipt resolves native fonts, colors, spacing and radii; existing brand tokens are unchanged. Changed state previews cover the accepted 1180×820 plus 375×844, 768×900, 1440×1000 and short 820×640 / 1180×680 sizes. Narrow layouts retain outer scrolling and the contact action dock.

Verification: release Swift build passed. Saved PNG/JSON evidence is in the canonical October 19 packet at `native-ui-renders/`; `integration-evidence.json` records source hashes, pass provenance, the accepted 1180×820 reference comparison and unverified boundaries. Preparation, contact/review and final status renders are retained separately so an earlier unchanged area is not misrepresented as a new full pass. Runtime fonts resolve to OpenSans-Regular / Arial-BoldMT and the existing sRGB/geometry tokens. No fixture executable or app bundle is included in that evidence folder. Privacy details pins `phone-run-v1` to `account-2026-10-v6`, and the new v2 disclosure to the agreed v7 Phone section.

Physical/signed qualification remains separate: Finder guide followed on a real phone; read-only recheck; encrypted/off/unknown availability; keyboard and VoiceOver interaction; system reduced-motion setting; real browser focus and final saved receipt. UI does not claim local Disconnect revokes account authority. Server Stop, replacement adoption and retained-run recovery require the coordinated protocol and cannot be proven by these previews.


## Current integration and release identity

| Item | Current authority |
|---|---|
| Native integration | `codex/phone-recovery-oct19`; coordinator owns bridge, integration, signed build and release evidence |
| UI implementation | `codex/phone-ui-oct19`; first UI commit `af5a43c` integrated as `5c6453c`; bounded run-status follow-up accompanies this handoff |
| Apple Silicon candidate | `rc29` / build `26101929` |
| Intel candidate | `rc29-intel1` / build `261019291` |
| Website implementation | `codex/fable-phone-google-oct19`; protocol, immutable notices and deployment remain website/integration owned |
| Physical acceptance | Jeff, on this Mac with his iPhone, explicitly reviews and Saves results |

The canonical [Phone website/native protocol](</Users/jeffmoore/Documents/🗣️ Voice Orchestrators/outputs/google-segment-two/phone-google-review-2026-10-19/PHONE-WEB-PROTOCOL-OCT19.md>) governs exact request/response shapes and recovery. Migration **0059 extends 0058**. New approval admission is checked by account, platform and verified build; build identity alone does not open admission. Existing-run recovery is a separate route from admitting another run.

Native parses v1 and v2 separately and opens the policy snapshot associated with the actual approval. No status response silently upgrades consent. New v2 approval uses the exact approved compact summary and agreement. An uncertain check preserves existing authority material and pauses new admission; fresh status, binding and intent verification are model-owned. Explicit website replacement adoption must follow the settled bind proof in the canonical protocol; do not substitute a restart or a second Save for same-transfer recovery.

## UI and fixture follow-up

The v2 status notice derives from existing `PanelStyle`, heading/body fonts, progress row and Secondary/Lime buttons. It appears for uncertain, expired, revoked or changed approval; successful periodic checks remain visually quiet. Retry acts on the current run. No repeated checkbox or destination dialog is added for an unchanged approved run.

The visible local Stop and account Stop navigation use existing button primitives. The account link only opens the account's run controls. It does not issue the authenticated Stop mutation. Completion continues to distinguish selected local totals, receipt-confirmed source Saves, the final browser receipt and local cleanup.

Focused verification: release Swift build and `Tests/check_fake_demo.sh` passed with the status/version core. Changed status/Stop renders are saved under `native-ui-renders/run-status-v2/`; its evidence receipt explicitly excludes the pre-adoption replacement-state image pending the final model contract.

The isolated FAKE DATA helper accepts the same paused/current authority control for its fictional handoff. Its checks explicitly choose available data after the encryption-off result; no real helper, phone, network or account is used. The offscreen renderer remains separate from this simulated helper test.

## Remaining qualification and execution

1. Integrate the exact native/model/helper and website protocol commits and complete required synthetic cross-version, recovery and data-integrity checks.
2. Verify the migration and account/platform/build gate against the intended deployment. Keep immutable v1/v2 notice identity and exact saved-transfer proof intact.
3. Build and qualify the signed RC29 artifacts with their own identity. Follow the coordinator's [real-device local test instructions](REAL-DEVICE-LOCAL-TEST.md); the retired RC28 alias procedure is not applicable.
4. Jeff runs the physical iPhone transfer, follows Finder/Trust/password prompts, reviews selected sources, explicitly Saves, and checks the receipt/report result. Signed/device acceptance is required before broad publication.

A release build, synthetic render or published artifact does not establish physical device/account acceptance. This UI handoff contains no claim that RC29 is installed, that real phone data was collected, or that an account report was saved.

## Historical source

[Original PR10](https://github.com/JmooreOsirs/amplifai-phone-companion/pull/10) and source commit `9730220a6c91c51d41749353cc5a038624734295` preserve the original proposal and review context. Its old release instructions, RC28 aliases and helper assumptions are superseded by this handoff and the canonical October 19 protocol.
