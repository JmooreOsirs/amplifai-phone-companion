# Windows frozen package — source-only plan

No Windows freeze command has been executed for this package. This is not a
download, installer, signing or ordinary-user acceptance receipt. Keep the
published Windows download gate closed. The canonical executor owns the
integrated Windows source and package qualification.

## Fixed inputs and shape

- Existing engine entry: `collector/agent_entry.py`, SHA-256
  `ea1d89e60a9aadf1bf7f9178c02425a9f454e44fe82233c52fe82f0836ab7053`.
- Existing Windows x64/CPython 3.12 lock: `collector/windows-requirements.lock`,
  SHA-256 `aa8ee933827681ebda483c2a889dc6156f7d9de906fe21d90f055fd06939c95d`.
  It pins PyInstaller 6.22.3; no new dependency/installer framework is proposed.
- Wrapper entry: `collector/windows/app.py`, integrated with the current engine
  source and its fixed resource contract. Its 33 scoped synthetic checks are
  source evidence, not frozen or rendered proof. The new
  `script/build_windows_candidate.py` has seven passing source-boundary tests;
  no Windows-built package has passed yet.
- Supplied original PNG, with no redraw: SHA-256
  `07dabb73a6c03cb400079b05fc2ac7b26042529674f0fe56f261f2028e731dce`.
  A Windows executable icon still needs a recorded proportional, deterministic
  derivation from that original and its own hash; no substitute logo is allowed.

```text
AmplifaiPhone/
  AmplifaiPhone.exe
  _internal/                         wrapper Python + Tcl/Tk
  resources/amplifai-by-nexus-original.png
  resources/phone-helper/
    AmplifaiPhoneHelper.exe
    _internal/                       complete separate helper runtime
```

Never merge the two `_internal` trees or copy only the helper EXE. Fixed resources
must remain contained regular files/directories, with no links/reparse escape or
PATH/source fallback. The helper keeps console stdio for private JSON-lines;
the wrapper may be windowed. [PyInstaller documents these build switches and
the stdio distinction](https://pyinstaller.org/en/stable/usage.html).

## Next bounded build, not authorized execution by this document

Use an isolated exact-source staging directory and the existing free standard
public Windows runner only after reviewing its changed source/workflow.
Record actual CPython/Tcl/Tk versions and install the existing lock with
`pip --require-hashes`. Adapt the existing `script/build_portable_candidate.sh`
freezer entry pattern, not its Mac Swift/signing or aarch64 interpreter inputs.
The new builder generates these invocations with distinct owned output/work/spec
paths; they still require actual Windows execution:

```powershell
python -m PyInstaller --onedir --contents-directory _internal --console --noupx --name AmplifaiPhoneHelper --paths source/collector --distpath helper-dist --workpath helper-work --specpath helper-spec source/collector/agent_entry.py
python -m PyInstaller --onedir --contents-directory _internal --windowed --noupx --name AmplifaiPhone --paths source/collector/windows --distpath wrapper-dist --workpath wrapper-work --specpath wrapper-spec source/collector/windows/app.py
```

Stage the entire first output into the second output's
`resources/phone-helper/`, and copy the exact logo separately into `resources/`.
Do not use `--add-data` for this external-onedir contract: resources are located
beside the launched `sys.executable`, not assumed to be under the wrapper's
`_internal`. [Runtime path behavior](https://pyinstaller.org/en/stable/runtime-information.html).
No UAC/admin flag, UPX, new credential, user, signing service or installer is added.

## Qualification before any release

1. Resolve reduced-token storage failure first: public `bea9923`, run
   [36910257572](https://github.com/JmooreOsirs/amplifai-phone-companion/actions/runs/36910257572)
   passed seven policy checks but failed ancestor pinning at native stage 15,
   before marker/data. Least-access ancestor review is a separate source change;
   exact root owner/DACL validation and no-share-delete pinning cannot be relaxed.
   Administrator proof `901142c`/run 36908082055 remains separate.
   The prepared source candidate requests FILE_READ_ATTRIBUTES only for ancestor
   identity pins, retaining READ_CONTROL plus FILE_READ_ATTRIBUTES on the root.
   Both still reject reparse tags and retain no-share-delete handles through
   marker/data admission. [Windows separates these access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights).
   The disposable probe observes the old mask first without admitting storage:
   numeric `win32_code`, `requested_access`, ancestor index and `api_code`
   (1 = CreateFileW, 2 = attribute readback). The new path must independently pass;
   there is no privileged fallback. Stage-15 READ_CONTROL attribution is still
   an inference until this changed candidate runs on actual Windows.
2. Actually run the frozen helper's `runtime-check` and `inspect` with no installed
   source/PYTHONPATH fallback. Test private pipe framing, residue recovery and
   owned process exit. Import success is not Apple USB-driver/phone coverage.
3. Run the actual frozen wrapper→helper pipe and GUI lifecycle. Confirm independent
   runtime loading, Tcl/Tk and original logo, keyboard/focus/DPI/accessibility and
   close/error paths. Environment reset alone is not loader proof:
   [PyInstaller's subprocess cautions](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html).
   Closing stdin does not interrupt synchronous capture; TerminateProcess still
   has unconfirmed cleanup. Browser `received` never means authenticated `saved`.
4. Inventory all Windows PE/runtime bytes and notices. Reuse
   `script/collect_portable_sources.py` with the Windows lock, but audit missing
   sdists and bundled CPython/Tcl/Tk/native DLL provenance separately. Mac's
   143-archive source offer cannot qualify different Windows binaries. Record
   exact source SHA, package/asset hashes, notices and complete matching source
   before any public asset; technical inventory is not a legal opinion.
5. Normal non-admin fresh-user install/SmartScreen/signing, Windows 11 physical
   iPhone Trust/password/USB and authenticated account-save gates remain separate.
   No installer readiness or public Windows link follows from a VM build alone.

The current free-run boundary has no artifact upload/cache storage. Artifact
delivery/storage, signing and installer decisions require their own reviewed
bounded route; this plan does not provision them. Existing Mac/Android public
releases and rollback artifacts remain frozen.

## Proposed next isolated build workflow, still not executed

After the numeric storage candidate passes and the exact GUI and shared-Python
source are reviewed, replace
the probe-only workflow with one finite changed-source build job, not a second
executor. Pin the same Actions; use standard public `windows-2025`, contents-read,
15-minute job bound, no caches/uploads/signing secrets. The job stages only reviewed
companion source. Fail each native command on its real exit code.

- Preflight exact source SHA, x64/Python and lock/logo hashes. Create a unique owned
  build directory, isolated venv and hash-required dependency install. Record the
  installed runtime versions without printing arbitrary environment values.
- Freeze the helper (bounded six minutes) and wrapper (three minutes) separately
  with the commands above. Keep generated specs/logs and both complete outputs in
  that one owned directory. Stage the fixed resources; enumerate/hash every staged
  relative file and confirm no link/reparse escapes or flattened DLL runtimes.
- With PYTHONPATH/PYTHONHOME absent, exercise frozen `runtime-check` and `inspect`
  (one-minute bounds) and the actual wrapper-owned private helper pipe. A successful
  source interpreter or PATH-installed package cannot satisfy frozen-runtime proof.
  Do not invoke `connect`, collect from a phone or fabricate a saved acknowledgment.
- Qualify actual frozen Tk startup, unchecked opt-in, logo, keyboard/close path and
  clean helper exit on the VM. A bounded window/controller probe must be reviewed
  before addition; synthetic Tk fixtures cannot replace this. Rendered snapshots
  and DPI/accessibility review remain their own evidence, not a successful build.
- Emit bounded source/package/runtime/hash results inline only. The VM outputs
  remain disposable and unshipped; no artifact storage or Windows download link
  is authorized here. Finish Windows-specific source/notices and ordinary-user
  installation/signing/physical-device qualification before proposing publication.
