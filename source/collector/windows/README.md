# Windows wrapper — integrated source candidate only

The Windows wrapper is integrated with the current native engine source. It is
not an installer, signed release, qualified Windows download, or completion of
O05/O22. No real phone, password, provider credentials, signing, publication
or deployment was used.

The wrapper uses Python's standard Tk interface, a private JSON-line helper
subprocess, and the existing engine's modes. No dependency is installed here.
The local macOS Python lacks `_tkinter`: UI import/render and actual Windows
freeze/install are **not verified**. Standard Python Windows distributions
include Tcl/Tk; the packaged runtime still must be checked on Windows.

## Verified finite increment

Run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s collector/windows/tests -v
```

This exercises the actual session/controller and an actual synthetic subprocess,
not a real collector. Headless callback tests substitute Tk and do not prove
rendering. Resource fixtures create only synthetic files in owned temporary
directories; no fixture imports device libraries or reads phone data.
The wire-contract regressions also run the actual engine's `run_connect` with an
injected synthetic capture, then feed its unchanged JSON packets into the session.
Review counts and `missing_sources` use the engine's snake_case fields; capture,
pairing and handoff packets retain their existing field names. Missing source
disclosure is required, not silently replaced by complete coverage. This proves
packet compatibility, not real device acquisition, browser saving or Tk rendering.
Production startup requires Windows, the frozen wrapper, the matching isolated
helper onedir and the exact original logo. No PATH/source fallback, loose helper
EXE or user-supplied executable is launched. `PACKAGE-INPUTS.json` is the input
contract. `script/build_windows_candidate.py` now has seven passing synthetic
boundary tests, but its two actual freezes and frozen runtime checks have not
yet run on Windows.

```text
AmplifaiPhone/
  AmplifaiPhone.exe
  _internal/                         wrapper runtime, including Tcl/Tk
  resources/
    amplifai-by-nexus-original.png   unchanged source logo
    phone-helper/                   entire dist/AmplifaiPhoneHelper/ tree
      AmplifaiPhoneHelper.exe
      _internal/                    matching helper runtime and native libraries
```

The resolver rejects missing, linked/reparse or unreadable resource components,
including nested helper runtime entries. Its containment/presence checks do not
prove a complete matching build, dependency hashes, signatures or installer
permissions; those remain qualification gates. The wrapper launches the fixed
helper with its own cwd, private pipes, discarded stderr and a copied child
environment without `PYTHONPATH`/`PYTHONHOME`. The public
`PYINSTALLER_RESET_ENVIRONMENT=1` control isolates the second frozen runtime;
private `_PYI_*` variables are not rewritten.

Contact replacement and password clearing explicitly enable disabled widgets
only for the atomic update, then apply the current guarded state. This follows
Tk's [listbox](https://www.tcl-lang.org/man/tcl9.0/TkCmd/listbox.html) and
[themed entry](https://www.tcl-lang.org/man/tcl9.0/TkCmd/ttk_entry.html) disabled
insert/delete contract; the headless fixture is not rendered Tk evidence.

## Collection and completion contract

- Initial checkbox is unchecked. Only explicit Agree creates fresh local,
  operation-bound approval; Connect consumes it. Agree alone does not collect.
  This is not a managed collection receipt, marketing permission or account-save
  approval. The website owns its independent authenticated receipt gates.
- Backup password is accepted only when the helper requests it, sent through
  private stdin, then cleared from the input. It is absent from argv, environment,
  files and logs. Immutable Python/Tk memory cannot promise forensic zeroization.
- Contact selection starts empty. Changes revoke any current handoff and require
  fresh review, including a pairing still in flight. Commands awaiting review,
  pair or status responses are serialized. Expected stale responses are consumed
  without showing old counts/codes or accepting a save acknowledgment. Pairing
  accepts only the engine's fixed port 48751, ten ASCII digits and five-minute
  exact Origin/Host bridge for selected metadata. No browser-security override
  is offered.
- Browser `received` is not `saved`. The model requires the matching helper
  handoff's `saved` acknowledgment, the guarded `finish` command, completed state,
  zero helper exit and empty marked-residue inspection before saying completed.
  The wrapper does not manufacture or send account-save acknowledgments.
- The account browser's save path is published, but no signed-in Windows-to-web
  saved ACK has been observed. A local preview cannot meet the authenticated
  save-ACK contract; keep the native review or explicitly discard it. No saving
  flag is changed by this wrapper.

## Cancellation dependency — do not blur this boundary

Current `amplifai_phone/agent.py` registers SIGTERM to raise `CaptureCancelled`,
but synchronous capture finishes before its stdin command loop. Closing stdin
can interrupt a requested password or later review; it does **not** guarantee
interruption of an in-flight capture. The UI says stop requested and waits.

Windows `Popen.terminate()` uses TerminateProcess, not graceful Unix SIGTERM.
Force stop is an explicit, warned action against only this owned helper. It
bypasses engine cleanup/finally and can leave private temporary backup content.
Confirmation is bound to the same pending process, never a replacement helper.
Oversized/malformed output, failed private writes and invalid model events close
stdin cooperatively; they never invoke force stop automatically. After a stream
failure, remaining stdout is discarded in bounded chunks until actual exit.
Cleanup remains unconfirmed throughout. A new inspection resets stopped-session
state but cannot reuse collection approval. Current native `workspace_marker`
errors explicitly explain that a private empty/marker-only root may remain and
no phone data was written by that attempt; no unsafe-root deletion is offered.
Every subsequent collection requires successful inspection; marked abandoned
sessions require separate explicit deletion confirmation. Nothing targets other
backups. Cooperative Windows capture cancellation is an engine dependency,
not implemented or claimed here.

## IR-16 reference contract

Authority: existing Mac companion `Brand.swift` and `ContentView.swift`, not a new
mockup. All navy/panel/border/body/muted/lime/blue values are exact source values.
Arial 22pt heading and 12pt body derive from the Mac's Arial 28px/14px hierarchy;
Windows DPI/render matching is an open gate. Native ttk controls implement the
existing primary lime button, panel/secondary and readable focus grammar.
The original `amplifai-by-nexus-original.png` is copied without modification;
in-window integer subsampling preserves its letterforms. No substitute A icon,
portrait, new illustration, font package, animation or fabricated claim is added.
One scrollable task flow keeps controls accessible on smaller/high-DPI windows;
contact list has native multi-selection and scrollbar. There is no motion.

## Remaining release gates / rollback

Windows compile/freeze, GUI rendering, keyboard/accessibility/high-DPI checks,
Tcl/Tk inclusion, helper dependency/native-library/Apple-driver qualification,
non-elevated private ACL/PID proof, cooperative capture cancellation, normal
fresh-user installer, code signing/SmartScreen, physical-device Trust/password
permissions, real cleanup recovery and authenticated cross-layer saved
acknowledgment remain open for this wrapper package. Separate engine tests and
administrator-runner checks do not qualify this unbuilt distribution.
No downloader, manifest, CI or site availability is changed here. Rollback is
removing these isolated `collector/windows` source files; no user data was changed.

Protocol and platform references: existing engine `agent.py`/`bridge.py`, and
[Python Tk documentation](https://docs.python.org/3/library/tkinter.html) /
[Windows subprocess termination](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.terminate) /
[PyInstaller frozen runtime](https://pyinstaller.org/en/stable/runtime-information.html) /
[PyInstaller environment reset](https://pyinstaller.org/en/stable/advanced-topics.html#pyinstaller-reset-environment).
