# Real-device local test of the one-approval flow: preparation only

**Status (2026-10-19): not staged for this flow, and never launched.** `source/script/stage_local_device_test.sh` was updated for the one-approval run, but it has not been run with this code. No real helper, collection, upload or account action has run, and the installed RC28 app was only read.

**The earlier staging is the old flow.** `/private/tmp/amplifai-real-test.20261019-061303/` (commit `4d3146d`) has the Mac destination sheet and no `amplifai-phone` link. It doesn't test this flow.

## The boundary: why a real test of this flow waits for the website

This flow can't run end to end until website PR #5 is live on an approved official origin:
- Its run starts on the website (**Agree and collect**), which needs migration `0058` applied and `NEXT_PUBLIC_PHONE_RUN_APPROVAL_RELEASE=enabled`.
- The Mac bridge accepts only `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`.
- The app's run authority is fixed to `amplifai-database-engine.vercel.app`.
- None of these origin or authority checks may be relaxed to test a preview.

So the order is: qualify and apply `0058`, deploy the website with the flag off, then use a test environment (or the release order in HANDOFF.md) to turn the flag on for a test account. Only then is a real test meaningful.

## What the updated script would stage (when run)

```bash
source/script/stage_local_device_test.sh
```

It builds this branch's Mac app and writes a new, never-reused `/private/tmp/amplifai-real-test.run-approval-v1.<timestamp>/AMPLIFai Phone LOCAL TEST.app` with a `local-device-test-receipt.json`.

| Part | Source |
|---|---|
| App binary | This branch, release build |
| Helper and Sparkle | Copied unchanged from the installed RC28 and verified byte-identical. The branch changes no helper code. |
| Bundle ID, channel, version, build | RC28's (`ai.satoris.amplifai.phone.candidate`, `26101628`), so the website status and diagnostics routes accept it |
| URL scheme | `CFBundleURLTypes` for `amplifai-phone` added to the **staged copy's** Info.plist only. The receipt records `runLinkSchemeDeclared` and `registeredWithLaunchServices: false`. |
| Updates | All `SU*` keys removed |
| Signature | Outer app ad-hoc signed; the helper and Sparkle keep their Developer ID signatures |

**Caution: link registration.** The staged copy keeps RC28's bundle identifier. Opening it lets macOS associate `amplifai-phone://` with that identifier, so a later link could go to RC28, which can't handle it. Run it only after RC28 is quit normally. Afterwards, expect the association to stay until LaunchServices updates. The script itself never runs `lsregister` and never launches anything.

## Prerequisites before any real test

1. **Consent:** the parent and Brian approve a real collection and a real save.
2. **One companion at a time:** RC28's status helper owns `127.0.0.1:48752`. Quit RC28 normally (AMPLIFai Phone menu → Quit) after confirming nothing is in progress. Never use `pkill`, and never use `build_and_run.sh` run, verify, debug, logs or telemetry modes.
3. **Shared session folder:** `~/Library/Application Support/AMPLIFai Phone Candidate/sessions` is shared with RC28. Review any temporary-data prompt; never clear it automatically.
4. **Timing:**
   - Start within 10 minutes of the app opening with the approval.
   - After **Continue**, the browser must connect within 5 minutes (sign in beforehand).
   - A released transfer stays open while it's active, with a 2-hour inactivity limit.
   - An expired connection can't be resumed. Choose **Discard review** in the app, then approve and collect again.
5. **Data scope:**
   - The full iPhone backup arrives on this Mac temporarily, and the helper reads contacts, calls and messages metadata for the review.
   - After **Continue**, the chosen contacts and their available call and message metadata are saved automatically to the approved account and course.
   - Message text, backup files and passwords never leave the Mac.
6. **Diagnostics:** the staged copy reports build `26101628`. A failure sends a safe code labelled with RC28's build. Decide whether that is acceptable.

## Checklist once the website is live

1. Website: sign in to the test account, then **Agree and collect** and **Open AMPLIFai Phone**.
2. Mac: the approved account, course and "Approved to save" appear. Choose **Start collection**; tap Trust, and enter the backup password if encryption is On.
3. Optionally let the collection run long (35 minutes is fine). Choose contacts, then **Continue**.
4. The Mac minimizes and the browser comes forward on the course page. It must never come back on its own.
5. Website: automatic save with progress → report → **Done — I reviewed these results** → **Print or save PDF**.
6. Also try: Cancel during the check and the transfer; revoke or sign out mid-run (the website asks once to approve again); let a connection expire.

Afterwards, quit the test app and reopen RC28 from `/Applications`. The staged folder in `/private/tmp` can be removed; leave the sessions folder alone.
