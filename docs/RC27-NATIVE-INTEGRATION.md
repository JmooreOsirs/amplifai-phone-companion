# rc27 native integration

## Work agreement

Outcome: qualify the rc27 account-origin and contact-selection fixes, preserving
later Windows/Intel qualification and the protected release controls. Approved
executor setting: GPT-6.1 Sol / High. Next milestone: a clean-source ARM build with
matching Legal/source payload and real frozen-helper/native qualification.

Completion requires immutable artifact/source hashes, independent protected review,
existing signing identity/key continuity, notarization and publication readback,
and coordinated website/feed deployment. Jeff will perform the physical iPhone
transfer and explicitly review/Save the results on this Mac. Automated fixture
qualification does not establish that physical acceptance.

## UI reference contract

Design authority is Brian's supplied rc27 source at
`cbd9e9c5a01f8ddc7260a4b3e9b4bfc317f1a6a4`, including summary-first contact
selection, scoped search, the Main default selection, and include-all control.
The integration retains those source files. Existing Brand colors, Arial type,
native panels, buttons, spacing and corner radii remain the component primitives.
Any additional UI change requires a mapped derivation and matched-viewport
offscreen rendered comparison against this source. Synthetic rendering must not
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
