# Windows companion source candidate

This wrapper presents an rc15-compatible local iPhone collector on Windows. It is **not a signed installer or supported download**. Mac rc16 and website Android rc9 are the published packages. The Windows candidate has no qualified Apple USB driver, signing identity, physical-phone, or authenticated hosted-save proof. A runner-user install is narrower than a normal non-admin customer install.

## Local collection and review

The unchecked collection agreement must be accepted after a clean temporary-data inspection. Connect consumes that approval. iPhone Trust and encrypted-backup password prompts remain user decisions; the password travels only over the private helper pipe and is cleared from the entry widget. No backup, password, message body, or attachment is sent to the browser.

The helper uses the shared disk-backed sanitized record store. The window keeps only the current 200-contact preview page plus selected contact IDs. Search, Next, and Previous preserve selection across pages. Review sends at most 1,000 IDs per command, with a new UUID and SHA-256 over the complete ordered ID set. The exact review ID, page cursor, selection count, and digest must return before pairing is offered. Changed selection revokes the current pairing; stale page, review, and handoff responses cannot grant a new one.

The browser's received state and saved parts awaiting final browser receipt cannot finish the native session. Only the matching bridge's verified saved state permits finish; the helper must then exit successfully and an empty marked-residue inspection must complete. A fresh pairing replaces the old bridge. The five-minute deadline applies to initial admission; an authorized active transfer uses the shared v2 keepalive and durable ACK contract.

This source candidate adds all-page Select all/Clear all and native approval of the server-confirmed account destination before code-free Connect. The helper now supports the fixed-origin `/v3/discover` contract; the temporary one-use code remains an explicit recovery path. The website still blocks Windows setup/download, so these source checks do not establish a customer installer or physical account Save.

The window accepts only the helper's bounded transfer counters; bytes checked, retained, discarded and locally processed are shown as measured counts, never as an invented time remaining or account-save percent. Optional Call/SMS failure reasons are fixed allowlisted codes, with Unavailable distinct from readable zero. The helper preserves the current rc15 selected-payload rejection codes; required Contacts and global failures remain fatal. Finalize and the per-source saved report live in the authenticated website after separate Save decisions, not in this window's capture progress.

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

The disposable public Windows runner uses the source-only codex/windows-x64-candidate-oct8 branch. On run 37862692679, the old Python temporary fixture failed ancestor index 6 because its owner did not match the effective reduced user. A fresh fixture created under the reduced token with the app's protected owner/DACL passed while every ancestor remained pinned; the candidate restored ancestor FILE_READ_ATTRIBUTES (0x80), root READ_CONTROL and attribute readback, no-share-delete, and no-follow reparse checks. Earlier run 37863879590 passed 38 wrapper tests, 23 storage checks with the reduced-token probe, and 201 engine tests, then opened/closed the frozen GUI. The rc12-compatible source at `47776e1bf149` produced a private unsigned MSI on Windows2025 runner 37975221058 with WiX 3.14.1.8722. Its exact 7,289-file / 104,079,534-byte inventory, MSI SHA-256 `71d0c59fd3649f20b98a640b5e70ae61c2430c57ef249d0e6840f1e90a9820ac`, runner-user install/uninstall, and private draft tag `windows-rc12-private-47776e1bf149` are recorded in the canonical project ledger. The draft is not a customer release.

The closed rc12 wrapper had 38 Windows synthetic/headless tests, including 25,001 selected contacts across 200-contact previews and 1,000-ID review commands, stale-response rejection, and final saved-ACK gating. The distinct private rc13 candidate at `43eb26bd342a8f9b14a2f2c3ff03e2ba3137ddd5` passed standard Windows2025/WiX 3.14 runner `37997875834`, including synthetic Tk controls, frozen helper, and runner-user MSI install/uninstall. Its **unsigned private draft** MSI is 41,001,852 bytes, SHA-256 `271655ac6a79780af108c6b6115b9beea210970f42ecd155e1975c022a7bbaea`. Independent 680 px render review found the four-button consent row clipped the Full privacy guarantee label. The next private candidate wraps those actions into two existing-style rows and checks their actual Tk bounds at 920 and 680 px. None of these checks establishes physical iPhone or authenticated account acceptance.

Before Windows distribution: qualify Apple driver and a normal non-admin installer/SmartScreen path, sign the exact installer, inspect keyboard and high-DPI rendering, and observe physical iPhone Trust/password/permissions through authenticated account save/reload. A private draft MSI retention is not a customer release. Keep the website Windows download disabled until all required gates close.
