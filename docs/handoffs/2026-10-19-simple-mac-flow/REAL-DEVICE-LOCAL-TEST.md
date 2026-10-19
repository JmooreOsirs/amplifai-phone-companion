# Real-device local test of this branch — build-only preparation

**Status (2026-10-19): staged, never launched.** No real helper, collection, upload or browser account action
has run, and the installed RC28 app was only read. A real test needs the consents and checks below first.

## What is staged

```bash
source/script/stage_local_device_test.sh
```

The script builds this branch's Mac app and stages
`/private/tmp/amplifai-real-test.<timestamp>/AMPLIFai Phone LOCAL TEST.app`, with a
`local-device-test-receipt.json`. The latest 2026-10-19 staging is `/private/tmp/amplifai-real-test.20261019-055958/`,
from commit `9d010df` with a clean tree. That commit has the same app source as the branch tip; later commits change only docs.

| Part | Source |
|---|---|
| App binary | This branch, `swift build -c release --arch arm64` (Command Line Tools). The toolchain's Swift back-deployment run path is removed; macOS 14+ supplies those libraries. |
| Helper (`Resources/helper`) and `Sparkle.framework` | Copied unchanged from the installed RC28, verified byte-identical. The branch changes no helper code. |
| Bundle ID, channel, version, build | Unchanged: `ai.satoris.amplifai.phone.candidate`, `macos-arm64`, `2026.10.16-rc28`, `26101628`. The website status and diagnostics routes accept only fixed identities. |
| Updates | All `SU*` keys removed, so the updater is disabled (status reports update state "manual"). |
| Name and location | "AMPLIFai Phone LOCAL TEST", outside `/Applications`. The exact executable path distinguishes it. |
| Signature | Outer app ad-hoc signed; the nested helper and Sparkle keep their Developer ID signatures. `codesign --verify --deep --strict` and the architecture check pass. |

This bundle is for pre-release testing only. Releases still come from Jeff's protected, signed and notarized
build.

## Prerequisites before any real test

1. **Consent:** the parent and Brian approve a real collection and a real share.
2. **One companion at a time:** the installed RC28's status helper listens on `127.0.0.1:48752`. While it runs, the test app shows "Another AMPLIFai Phone app is running" and blocks collection, by design. Brian quits RC28 normally (AMPLIFai Phone menu → Quit) after confirming no collection or unsaved review is in progress. Never use `pkill`, and never use `build_and_run.sh` run, verify, debug, logs or telemetry modes, which `pkill -x AmplifaiPhone`.
3. **Shared session folder:** `~/Library/Application Support/AMPLIFai Phone Candidate/sessions` is shared with RC28; it held one entry on 2026-10-19. The test app reads it. If it reports earlier temporary data, review the prompt; never clear it automatically, and keep everything else in place.
4. **Origin boundary:** the Mac bridge accepts only `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`, and account intents are bound to Production. A real test now therefore runs against today's Production website, which still has per-source Save and Finalize. The one-step **Save and view report** and report **Done** from the linked private website PR can only be validated on a real device after that PR is reviewed and released to an approved official origin. Do not relax origin checks to test a preview.
5. **Website routing:**
   - Production `amplifai.satoris.ai` (website `main` `7dd7917`) accepts build `26101628`.
   - The account page finds the Mac through localhost: `127.0.0.1:48751` is the one-time bridge, opened by the helper during pairing, and `127.0.0.1:48752` is status. The Mac has no custom URL launch.
   - After review the app opens `https://amplifai.satoris.ai/phone/account?continue=reviewed`.
6. **Destination:**
   - The Mac sheet "Share your reviewed contacts with this account?" shows the account email and course from AMPLIFai's authenticated server. Confirm both match the intended account and course before choosing Share.
   - These could not be read in advance: the built-in browser had no AMPLIFai session, and no new sign-in was attempted.
7. **Data scope:**
   - The iPhone's full backup arrives on this Mac temporarily. Only the contacts, calls and messages databases are kept for the review.
   - After Share, the selected contacts' names and phone numbers and their matching call and message metadata go to the signed-in browser. Message text, backup files and passwords never do.
   - Nothing is saved until Save is approved for each source in the account.
8. **Diagnostics:** the test build reports build `26101628`. If a collection fails, a safe code (build, stage, category, time, byte counts; no records) is sent labelled with RC28's build. Decide whether that is acceptable for the test.

## Launch and verification (only after the prerequisites)

```bash
open -n "/private/tmp/amplifai-real-test.20261019-055958/AMPLIFai Phone LOCAL TEST.app"
```

```bash
pgrep -fl "AMPLIFai Phone LOCAL TEST.app/Contents/MacOS/AmplifaiPhone"
```

```bash
lsof -nP -iTCP:48752 -sTCP:LISTEN
```

The status listener's path must be inside the LOCAL TEST bundle (`.../AMPLIFai Phone LOCAL TEST.app/Contents/Resources/helper/amplifai-agent`), not `/Applications`.

Then follow the real-device checklist in [HANDOFF.md](HANDOFF.md):
1. Check iPhone (USB, Trust).
2. Agree and Start; enter the password if encryption is On.
3. Try Cancel during the check and during the transfer.
4. Choose all, some or none, then Continue to account.
5. The browser connects; on the Mac sheet choose Share with this account.
6. On the Mac, Continue in browser. With today's Production website: approve and Save each source, then Finalize. After the website PR is released: Save and view report.
7. Mac: Saved in your account → Done. Website: the report, then Done when it is offered.

Afterwards, quit the test app and reopen RC28 from `/Applications`. The staged folder in `/private/tmp` can be deleted; the sessions folder must be left alone.
