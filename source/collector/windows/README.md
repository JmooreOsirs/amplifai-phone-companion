# Windows companion source candidate

This wrapper presents the rc12-compatible iPhone collector on Windows. It is **not a signed installer or supported download**. Mac rc12 and Android rc8 remain the published packages. The Windows candidate has no Apple driver, ordinary non-admin installation, signed binary, physical-phone, or authenticated hosted-save proof.

## Local collection and review

The unchecked collection agreement must be accepted after a clean temporary-data inspection. Connect consumes that approval. iPhone Trust and encrypted-backup password prompts remain user decisions; the password travels only over the private helper pipe and is cleared from the entry widget. No backup, password, message body, or attachment is sent to the browser.

The helper uses the shared disk-backed sanitized record store. The window keeps only the current 200-contact preview page plus selected contact IDs. Search, Next, and Previous preserve selection across pages. Review sends at most 1,000 IDs per command, with a new UUID and SHA-256 over the complete ordered ID set. The exact review ID, page cursor, selection count, and digest must return before pairing is offered. Changed selection revokes the current pairing; stale page, review, and handoff responses cannot grant a new one.

The browser's received state and saved parts awaiting final browser receipt cannot finish the native session. Only the matching bridge's verified saved state permits finish; the helper must then exit successfully and an empty marked-residue inspection must complete. A fresh pairing replaces the old bridge. The five-minute deadline applies to initial admission; an authorized active transfer uses the shared v2 keepalive and durable ACK contract.

This Tk window remains a **manual-code, 200-contact-page** interface. It does not implement Mac rc12's all-page Select all/Clear all or native confirmed-account code-free approval. The current website detects that this helper lacks `/v3/discover` before creating an account connection intent or clearing an existing review, then offers the temporary-code path. The rc12-derived helper includes Contacts/Messages size validation and typed optional-source evidence, without implying that the Windows GUI exposes every Mac feature.

Stop closes the helper's input cooperatively. It cannot promise interruption of synchronous capture. A separately confirmed Force stop targets only the owned helper, bypasses cleanup, and requires a new residue inspection. Failed cleanup is reported, never counted as completion or silently deleted.

## Package shape and qualification

The build_windows_candidate.py script freezes distinct console helper and Tk window runtimes into a disposable onedir candidate. The separate build_windows_msi.py script authors an unsigned WiX 3 per-user MSI only after the frozen package's exact file hash inventory and legal files match:

    AmplifaiPhone/
      AmplifaiPhone.exe
      _internal/                         Tk and wrapper runtime
      resources/amplifai-by-nexus-original.png
      resources/phone-helper/
        AmplifaiPhoneHelper.exe
        _internal/                       complete separate helper runtime

The wrapper accepts only these contained regular resources, with no linked runtime or PATH/source fallback. It launches the fixed helper with private stdin/stdout, discarded stderr, its own directory, and a copied environment without PYTHONPATH or PYTHONHOME. The original logo bytes are unchanged. PACKAGE-INPUTS.json records the input shape. The Windows x64 Python 3.12 dependencies are hash-pinned in collector/windows-requirements.lock.

The disposable public Windows runner uses the source-only codex/windows-x64-candidate-oct8 branch. On run 37862692679, the old Python temporary fixture failed ancestor index 6 because its owner did not match the effective reduced user. A fresh fixture created under the reduced token with the app's protected owner/DACL passed while every ancestor remained pinned; the candidate restored ancestor FILE_READ_ATTRIBUTES (0x80), root READ_CONTROL and attribute readback, no-share-delete, and no-follow reparse checks. Prior run 37863879590 passed 38 wrapper tests, 23 storage checks with the reduced-token probe, and 201 engine tests, then opened/closed the frozen GUI. Its old disposable inventory SHA-256 was `2deaa6a138cb85d67e724970e70724df5806ff0ef9686a50d4836e4bc9486c23` across 7,286 files / 102,032,826 bytes. The current rc12-compatible installer attempt must produce its own runner, compiler, payload and MSI receipts before any new binary claim.

The current wrapper has 38 passing Windows synthetic/headless tests, including 25,001 selected contacts across 200-contact previews and 1,000-ID review commands, stale-response rejection, and final saved-ACK gating. These are protocol checks, not rendered Windows UI or physical iPhone acceptance.

Before Windows distribution: qualify Apple driver and a normal non-admin installer/SmartScreen path, sign the exact installer, inspect keyboard and high-DPI rendering, and observe physical iPhone Trust/password/permissions through authenticated account save/reload. A private draft MSI retention is not a customer release. Keep the website Windows download disabled until all required gates close.
