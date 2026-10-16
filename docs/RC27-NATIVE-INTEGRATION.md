# rc27 native integration

## Initial integration work agreement

Outcome: qualify the rc27 account-origin and contact-selection fixes, preserving
later Windows/Intel qualification and the protected release controls. Approved
executor setting: GPT-6.1 Sol / High. Next milestone: a clean-source ARM build with
matching Legal/source payload and real frozen-helper/native qualification.

Completion requires immutable artifact/source hashes, independent protected review,
existing signing identity/key continuity, notarization and publication readback,
and coordinated website/feed deployment. Jeff will perform the physical iPhone
transfer and explicitly review/Save the results on this Mac. Automated fixture
qualification does not establish that physical acceptance.

## Initial integration reference (superseded for presentation below)

Design authority is Brian's supplied rc27 source at
`cbd9e9c5a01f8ddc7260a4b3e9b4bfc317f1a6a4`, including summary-first contact
selection, scoped search, the Main default selection, and include-all control.
The integration retained that behavior. The October 16 Mac-first contract below
supersedes this earlier presentation baseline with the accepted Google design.
Additional UI changes require a mapped derivation and matched-viewport
offscreen rendered comparison against that authority. Synthetic rendering must not
start collection, read Jeff's data, activate a window, or manipulate his app.

## Build and source delivery

`build_portable_candidate.sh --isolated` builds ARM with the checksum-pinned
CPython 3.12.12 provider archive and hash-locked dependencies.
`qualify_macos_candidate.py` copies that raw app to its exact delivery name,
stages the five Legal files, creates the corresponding source ZIP, and invokes
the producer against the real frozen app. All output uses a new temporary
directory. Installed apps and published artifacts are not staging targets.

The source ZIP contains all current tracked authored files. Only dependency
sources, notices, provider recipes and matching SBOM evidence are retained from
the checksum-pinned published baseline. Historical app inventories, old product
source and finalization receipts are excluded. A current source map binds every
actual Mach-O file to source/notice members and its pre-sign hash. Unknown native
components, changed dependency pins/SBOMs, unsafe archive members, or dirty source
fail qualification. The payload manifest omits itself and the map; later signing
evidence has two explicit append-only paths. This is technical attribution, not
a claim of binary reproduction or legal clearance.

The ARM workflow has a secret-free preview mode and a release mode that requires
protected `release/native` provenance and current protected-main authorization.
Preview evidence grants no signing authority. The privileged signing/publication
workflow independently verifies source, run, artifact, custody and fresh app hashes.

## Platform boundaries

The rc27 Windows account-origin source fix is retained alongside the later rc19
fixture/handoff qualification. Changed Windows bytes require a new version; they
must never overwrite published rc19 assets. Intel retains its Python 3.12.15
closure and current successful-artifact retention. The Intel recipe now uses rc27-intel1 / 261015271 and the same source/Legal
producer hook. Actual Intel compilation and frozen qualification remain required
before publication.

No new keys, accounts, spend, role changes, or personal collection data are part
of this build milestone. Source/workflow changes still require independent review.

## Mac-first design contract — October 16 resume

Authority: accepted Google preview at `8af608f715fe0003f219e11acb75462cf8238d70`, `/design/account?state=week2-review`, and its actual Google CSS. At the initial inspection, Fable cc317d1 was clean with no newer native implementation. The later b1a5247 specification is reconciled below without replacing newer behavior. Screenshot of the public Mac app is a problem report, not current RC27 source.

Work agreement: Jeff selected GPT-6 Astra / Ultra for this design phase, replacing Sol / High. The coordinator's task controls accepted those settings; this is a control receipt, not runtime-model attestation. Finite return point: reconciled design contract, actual Mac render, and 5,501-contact selection/navigation evidence. Ordinary implementation effort is recommended after that milestone. No installation, physical collection, account Save, signing-key export or release-gate bypass is part of the visual fixture.

- Existing native stack is SwiftUI/AppKit, shared by ARM and Intel. Reuse the real CollectorModel/ContactWorkspace and native controls. Do not add a web renderer.
- Palette matches exactly: background #0f1525, panel #141d30, border #263146, heading #edeff3, body #c8cfd9, muted #a1a9b5, lime #a6e63d, blue #5c87df. Body Open Sans, headings Arial bold. Bundle the official OFL font and notice for offline native use.
- Google raised person card → native lazy adaptive card grid. Preserve 15/13 type, 10px card radius, 12px card/well inset, selected lime outline and visible checkbox. The first slice used a 256pt minimum with inline source disclosures; the Fable reconciliation below reduces this to 220pt after moving secondary details into an accessible menu/popover.
- Controls → separate recessed toolbar, visible global Include all and scoped Select all/Deselect all with exact full-dataset counts. Search explicitly names name-only matching. All/Main/Review retain existing meaning; organizing marks stay secondary and visually distinct.
- Decision surface → persistent bottom Next dock, selected count and explicit separate account Save language. Never duplicate Next in the scrolling body. Pending review/share/save locks and current confirmation remain authoritative.
- Spacing 12/20/28; outer review expands up to 1440pt. Well height derived from native window height (260–520pt); Google's 322px stage cap remains unchanged in website. Default native window 1180×820. Controls reflow; outer body and inner well scroll independently. The Fable density pass verifies actual columns rather than copying the specification's inconsistent width arithmetic.
- Surface depth: panel, recessed well and raised card gradient derived from actual Google colors/shadows. Semantic radii 8/10/12; native keyboard focus and ≥24pt targets. No movement is added; reduced motion has no hidden/lift effect.

State map: setup/encryption choices/explicit collection approval → connect/transfer/process → password when required → full-dataset preparation → selection → immutable local review → one-time pairing and destination approval → browser received → account receipt pending → completed; errors, source unavailable, empty, revoked selection change and retained review remain explicit. Each state uses the same typography/surfaces; collection/consent/save model and disclosure versions stay intact. ARM/Intel share the implementation; Windows must translate the contract into its existing toolkit after Mac acceptance, not claim native platform testing from Mac fixtures.

Verification milestone: actual offscreen SwiftUI renders at 375/768/1440, short window, all/main/review/search/empty/loading/locked states; compare measured Google reference tokens and native geometry. Existing tests extended to 5,501+ with last-page exceptions, scoped selection, marks and page navigation. No real collection, device actions, source Save, app installation or signing in visual fixtures. Prior source 4875 and f9 Intel preview evidence remain distinct from this changed candidate.

### Windows parity implementation map

Keep Python Tk/ttk (`clam`), PyInstaller and the separate matching helper. Translate
the same reference tokens and state semantics after the Mac review milestone.

| Responsibility | Existing seam | Required change |
| --- | --- | --- |
| Presentation | `source/collector/windows/app.py`: styles, layout, contact render, refresh | Open Sans body, adaptive checkbox cards, recessed controls, persistent primary action |
| Selection workspace | `source/collector/windows/session.py`: all-page scan and selected IDs | Retain verified classification data; Main default, All/Main/Review, full-scope selection and counts |
| Organizing | Session model and view controls | Separate move marks, membership changes, explicit confirmation and Undo |
| Packaging | `source/script/build_windows_candidate.py`: GUI_FILES/resources | Include any new modules and the exact font/license in source inventory and app |
| Identity and evidence | updater, candidate/MSI builders, PACKAGE-INPUTS | New coordinated version/build/MSI identity; retain UpgradeCode, channel and namespace |

The existing Windows scan ignores search for global Select all and retains IDs
only; relabeling it as scoped selection would be incorrect. Preserve 200-contact
paging, 1,000-ID immutable review pages, >25,000-ID support, and handoff/Save locks.
Published rc19 and its pinned workflow artifact must remain immutable. Mac
offscreen renders do not establish Windows rendering, focus, DPI or package
acceptance. No Windows product changes are included in this Mac milestone.

### Fable handoff reconciliation

The source specification at Fable commit `b1a5247f8d6b74d13e085076a6b81158196b05a5`, `docs/overhaul/handoff/CODEX-PROMPT-2026-10-16-COMPANION.md`, arrived after the first native slice `c3bdd955555f77516dd2fa4381da9d9d0f28e88a`. It is a specification, not a newer native implementation. This slice reconciles its applicable details with the accepted rendered Google reference:

- Flat two-row desktop controls, group chips, quiet scope actions, and a title count replace the earlier explanatory wall. Search remains honestly name-only.
- Native Toggle controls render a 22pt checkbox and explicit mixed state. Native focus drives the shared ring; no custom keyboard interception or added motion.
- Each compact card has an always-visible 24pt actions menu, also available by context menu. Move and complete source details remain accessible when organizing is hidden. Full masked phone endings/count and the complete name/reason remain in the source popover; truncation never discards those details.
- Organizing remains secondary with separate marks, bulk confirmation and Undo. The original group explanation remains available inside that secondary path.
- Primary controls are 44pt; status stages, actual elapsed/byte metrics, local review metrics, pairing code, consent details and password entry use the same surfaces. Diagnostic, consent, disclosure, pairing and Save sentences remain authoritative.
- Open Sans body/Arial heading follows the accepted website, resolving the handoff's Arial-only proposal. The 375pt minimum remains supported. The smaller responsive logo is retained to keep Disconnect legible at that width.
- Compact cards use a measured 220pt minimum: the specification's 196pt/four-column arithmetic does not match the real padded window. Native well height remains responsive, while the website's stage cap stays unchanged.
- Optional hover lift and command-key shortcuts are deferred; built-in Toggle, Button and Menu keyboard semantics remain. Actual native keyboard/focus/menu interaction is not established by offscreen bitmap rendering.

The existing service/workspace, collection protocol, bridge, updater identity, disclosure version and signing keys are untouched. The view initializer accepts an optional initial presentation choice solely for rendering the unchecked consent screen; it does not approve collection or bypass the model's consent guard.

Release naming remains a coordinator-owned decision: combined RC28 versus RC27 then RC28. The proposed 26101528 is not activated here; Intel needs a coherent architecture-specific identity. No release identifier or feed is changed by this design slice.

Android later-parity sources are now located but differ: canonical native main tracks `source/collector/android/app/src/main/java/ai/satoris/amplifai/phone/MainActivity.java` at Android commit 634dcdac0697cf12e0690560f4b50ad7c17c1aab, version 26092802 / 2026.09.28-rc2; the website checkout separately tracks `collector/android` at 28e2cce, version 26100101 / 2026.10.01-rc1. The coordinator verified those pointers. Neither baseline is imported here or asserted to match the public APK. Public release provenance is the next Android dependency; Mac acceptance comes first.


### Final Mac design qualification

The production views compile in the offscreen NSHostingView fixture. The final
5,501-contact flow and byte-progress checks pass, including the final 101-contact
page, scoped selection, organizing marks, immutable selection pages and navigation
locks. Source-offer guards (9), Intel guards (13) and release-control tests (32)
passed during this slice; their covered source was unchanged afterward. The
bounded DoD-4 review found no actionable introduced regression or unused primitive.

Evidence is saved under the existing protected-native-release/mac-design folder:

| Evidence | Scope |
| --- | --- |
| fable-state-render | 24 synthetic states at six viewports, plus board/overview renders; before the final density and short Review-tag refinements |
| fable-density-render | Final 220pt minimum and responsive controls; Main/Review at six viewports and standalone boards |
| fable-tag-render | Final compact Review tags at six viewports and boards; complete source details remain available in the popover |
| fable-evidence-manifest.json | SHA256 and size for all 192 layered files; explicitly identifies superseded details and unobserved acceptance |
| fable-final-source-hashes.json | Exact production, fixture and font source hashes for the final render |
| final-card-geometry.json | Pixel measurements from existing final density renders; root-window and standalone-board geometry distinguished |
| final-text-contrast.json | Final text contrast ratios; lowest measured normal-text ratio is 5.41:1 |

The checked root-window layouts show one column at 375pt, two at 768pt, three at
820pt, four at 1180pt and five at 1440pt. The persistent Next dock remains visible
in the checked short-window states. Synthetic rendering creates no window,
activates no app, starts no collector/updater and reads no owner workspace.

Offscreen rendering does not establish native keyboard, focus traversal, menu
interaction, installed-package behavior, physical iPhone transfer or account Save.
The historical ARM source4875 and successful Intel f9 preview are separate from
this changed UI/font candidate. A new exact-source protected package qualification
is required once naming and review gates are resolved.

The website owner subsequently supplied the reconciled Preview source
1a042236ccd4cd643162b6aa44813ac2736a9246 and Ready deployment
dpl_AMYC5gzqzh3VS1WLQPfTPdvgnToN. Its flat controls and scoped/reset wording are
consistent with this contract; the website retains its 322px stage. This is a
coordinator-supplied website receipt, not native runtime or production evidence.
Current web reference:
https://amplifai-database-engine-lf4vde9kz-osirs.vercel.app/design/account?state=week2-review

The design-phase return milestone is met. Recommend ordinary Sol/High for release
execution through the coordinator; no model restoration is claimed here. PR4/PR5
independent approval, version resolution, protected credential custody and actual
sign/notary/publication remain open. Public RC26 and installed apps are unchanged.
