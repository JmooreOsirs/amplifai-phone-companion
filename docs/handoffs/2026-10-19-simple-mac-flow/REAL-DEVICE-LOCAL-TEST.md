# Signed RC29 physical iPhone acceptance

Jeff is the physical tester, on this Mac with his iPhone. Implementation and coordinated publication are authorized; customer approval, contact choice and report review remain Jeff's actions. The candidate has not yet completed signed physical acceptance.

## Exact candidate boundary

| Channel | Candidate version | Build |
|---|---|---|
| Apple Silicon | `2026.10.19-rc29` | `26101929` |
| Intel Test | `2026.10.19-rc29-intel1` | `261019291` |

Use the protected, independently reviewed source and qualification receipts, the existing Developer ID/Keychain signing setup, notarization and stapling. Record the exact app/package hashes and source/control commits before testing. Intel qualification executes on its native Intel runner; signing it on this ARM Mac is not physical Intel acceptance.

The former `stage_local_device_test.sh` has been removed. It copied the old RC28 helper, ad-hoc signed a changed wrapper, and kept RC28's identity. RC29 changes both helper and wrapper, so that recipe cannot qualify this release. Its historical implementation remains in PR #10 / commit `9730220`.

## Preconditions

1. The website owner qualifies the exact migration and obtains the required migration approval. `0059` extends `0058`; no production SQL is implied by release permission.
2. The website candidate implements the shared October 19 Phone protocol and immutable disclosure/policy snapshots. Fixed authority and allowed official origins remain unchanged. No preview-origin bypass is permitted.
3. Admission is scoped to the approved account, selected platform and verified compatible build. RC28, Windows and Android retain their supported paths. Existing-run recovery is separate from new-run admission.
4. Stage the exact signed RC29 candidate before broad feed/download publication. Quit any existing companion normally only after confirming no collection is active. Preserve temporary session data and let Jeff handle any recovery prompt.
5. Present the exact candidate and one short next action through the coordinator. Do not send a generic test request before the app and website are ready.

## Owner sequence

1. Sign in to the intended account/course. Review the upfront Phone disclosure and select **Agree and collect**, then **Open AMPLIFai Phone**.
2. Confirm the account, course and selected source categories shown in the app. Connect, unlock and trust the iPhone. Start within the original ten-minute Start window.
3. If backup encryption is off, follow the Finder guide or explicitly continue with available data. The app's **Recheck** reads the setting; it does not enable encryption or start collection. Any backup password stays in the private local collection flow.
4. Choose contacts and select **Continue**. Under the same valid run approval, the website saves the selected contacts and available approved metadata automatically. The Mac must not raise itself after handing off.
5. Review the saved report and select its report-bound Done action. Confirm return-login readback and final native receipt/cleanup. Record source outcomes truthfully; unavailable data is not a successful empty extraction.

## Required recovery evidence

Exercise the bounded approved acceptance cases against the exact signed candidate: uncertain current status, explicit account Stop, retry of the same verification, committed-response loss recovery, and final receipt retry. Preserve the exact run/handoff on retry and verify that saved sources are not saved twice. A local **Stop on this Mac** is not an account revocation claim. The account Stop action has its own verified server result.

Synthetic tests, offscreen renders, hidden AppKit teardown checks, signing, notarization and public-byte readback are distinct evidence layers. None substitutes for this physical/account acceptance. Broad publication follows the required acceptance and coordinated website gates. Keep migration history and prior release artifacts; Sparkle rollback requires a new higher build.
