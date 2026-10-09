# Windows companion packaging status

This is a source-only Windows candidate. No Windows installer, signed binary, or supported download has been published. Mac rc9 and Android rc6 remain the live packages. The exact source and run receipts belong in the canonical PROJECT_PLAN.md ledger.

## Fixed package contract

- `script/build_windows_candidate.py` freezes two distinct Windows x64 CPython 3.12 onedir runtimes: console `AmplifaiPhoneHelper` for private JSON-line collection and windowed `AmplifaiPhone` for review and pairing. The full helper tree is staged under `AmplifaiPhone/resources/phone-helper/`; the original unchanged logo is staged under `resources/`. Each retains its own `_internal/`.
- `collector/windows-requirements.lock` is pinned to SHA-256 `aa8ee933827681ebda483c2a889dc6156f7d9de906fe21d90f055fd06939c95d`; the supplied PNG is pinned to `07dabb73a6c03cb400079b05fc2ac7b26042529674f0fe56f261f2028e731dce`. Repository attributes force LF for the lock and binary handling for the PNG on the Windows checkout. Dependency installation requires hashes.
- The builder rejects linked/reparse inputs and outputs, checks fixed bytes, runs frozen helper `runtime-check` and `inspect`, and writes a sorted per-file SHA-256 receipt plus inventory digest. The disposable runner has no artifact upload, cache, signing, or publication step.
- The wrapper resolves only contained regular package resources and launches the fixed helper with private pipes and cleaned Python environment. Source or PATH fallback is forbidden. Browser `received` never means account `saved`.

## Changed-source qualification

Public source branch `codex/windows-x64-candidate-oct8` first reproduced Win32 5 at ancestor index 6 of a Python-created temporary fixture. A same-token fixture created with the application's explicit user owner and protected DACL passed the real reduced-token probe while retaining all ancestor pins, root READ_CONTROL and attribute readback, no-share-delete handles, and reparse rejection. Restored production ancestor access is FILE_READ_ATTRIBUTES (0x80). Run 37863879590 on Windows Server 2025 / CPython 3.12.10 passed 38 wrapper tests, 23 storage checks with the reduced-token probe, and all 201 current engine tests. Its exact lock/logo bytes passed, the distinct helper and Tk runtimes froze, frozen helper runtime-check/inspect passed, and the GUI opened, reached clean-inspection readiness, then closed normally. Disposable inventory: 7,286 files / 102,032,826 bytes / SHA-256 `2deaa6a138cb85d67e724970e70724df5806ff0ef9686a50d4836e4bc9486c23`. No binary was retained or published. The runner is not a real phone or authenticated account-save test.

The Windows wrapper uses 200-contact preview pages, cross-page selected IDs, 1,000-ID review commands with complete-set UUID/hash binding, and the shared immutable v2 transfer/save-ACK contract. Its README and PACKAGE-INPUTS.json state the current source behavior and remaining limits.

## Distribution gates

Before enabling a Windows download, preserve exact package/source/runtime/notice inventory and qualify a per-user non-admin installer, code signature and SmartScreen path, Tcl/Tk and Apple driver inclusion, rendered keyboard/focus/high-DPI behavior, ordinary-user protected storage, physical iPhone Trust/password/permissions, cleanup/cancel recovery, and authenticated account save/readback/reload. A disposable CI freeze cannot establish those. If any external gate remains, leave the Windows website option unavailable and continue the independent Android native progress/results milestone.
