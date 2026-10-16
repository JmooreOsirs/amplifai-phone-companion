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

Authority: accepted Google preview at `8af608f715fe0003f219e11acb75462cf8238d70`, `/design/account?state=week2-review`, and its actual Google CSS. Fable cc317d1 is clean with no newer native implementation; incoming concrete deltas must be reconciled without replacing newer behavior. Screenshot of the public Mac app is a problem report, not current RC27 source.

Work agreement: Jeff selected GPT-6 Astra / Ultra for this design phase, replacing Sol / High. The coordinator's task controls accepted those settings; this is a control receipt, not runtime-model attestation. Finite return point: reconciled design contract, actual Mac render, and 5,501-contact selection/navigation evidence. Ordinary implementation effort is recommended after that milestone. No installation, physical collection, account Save, signing-key export or release-gate bypass is part of the visual fixture.

- Existing native stack is SwiftUI/AppKit, shared by ARM and Intel. Reuse the real CollectorModel/ContactWorkspace and native controls. Do not add a web renderer.
- Palette matches exactly: background #0f1525, panel #141d30, border #263146, heading #edeff3, body #c8cfd9, muted #a1a9b5, lime #a6e63d, blue #5c87df. Body Open Sans, headings Arial bold. Bundle the official OFL font and notice for offline native use.
- Google raised person card → native lazy adaptive card grid. Preserve 15/13 type, 10px card radius, 12px card/well inset, selected lime outline and visible checkbox. Native minimum card width256pt is a documented derivation for 24pt targets and source/group disclosures; no single-column desktop rail.
- Controls → separate recessed toolbar, visible global Include all and scoped Select all/Deselect all with exact full-dataset counts. Search explicitly names name-only matching. All/Main/Review retain existing meaning; organizing marks stay secondary and visually distinct.
- Decision surface → persistent bottom Next dock, selected count and explicit separate account Save language. Never duplicate Next in the scrolling body. Pending review/share/save locks and current confirmation remain authoritative.
- Spacing12/20/28; outer review expands up to1440pt. Well height derived from native window height (260–520pt); Google's322px stage cap remains unchanged in website. Viewport375 collapses to one column,768 supports two when available,1440 supports four. Default native window1180×820. Controls reflow; outer body and inner well scroll independently.
- Surface depth: panel, recessed well and raised card gradient derived from actual Google colors/shadows. Semantic radii8/10/12; native keyboard focus and ≥24pt targets. No movement is added; reduced motion has no hidden/lift effect.

State map: setup/encryption choices/explicit collection approval → connect/transfer/process → password when required → full-dataset preparation → selection → immutable local review → one-time pairing and destination approval → browser received → account receipt pending → completed; errors, source unavailable, empty, revoked selection change and retained review remain explicit. Each state uses the same typography/surfaces; collection/consent/save model and disclosure versions stay intact. ARM/Intel share the implementation; Windows must translate the contract into its existing toolkit after Mac acceptance, not claim native platform testing from Mac fixtures.

Verification milestone: actual offscreen SwiftUI renders at375/768/1440, short window, all/main/review/search/empty/loading/locked states; compare measured Google reference tokens and native geometry. Existing tests extended to5,501+ with last-page exceptions, scoped selection, marks and page navigation. No real collection, device actions, source Save, app installation or signing in visual fixtures. Prior source4875 and f9 Intel preview evidence remain distinct from this changed candidate.

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
