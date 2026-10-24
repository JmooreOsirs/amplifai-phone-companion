# RC13 completion presentation contract

Authority is the accepted Android RC12 source at cccef9fe593d5ab72e36c648596a3ff617e876c2 and its existing native controls. The design-system skill is applied only to the new confirmed-completion state; no new visual system or component is introduced.

| Changed presentation | Existing primitive |
|---|---|
| Confirmed status | Existing handoff saved status resource and progress panel completed state |
| Open saved account and report | Existing handoff button, sizing, enabled styling and native accessibility behavior |
| Completion explanation | Existing handoff reason and review text views, using Android string resources |
| Setup status after browser return | Existing setup message area; no restored approval, run authority, source rows or permissions |

Typography, palette, side spacing, native button sizing, focus behavior and reduced-motion behavior are unchanged. Existing normal and IME selection layouts remain the reference; Select all, Deselect all, source availability, selection freezing and Cancel continue through their current state guards.

Qualification must exercise the signed APK on owned API29/API36 runtimes, including the existing compact/IME selection cases and the new native_setup_completion_v2 fixture. The completion case backgrounds the Activity, delivers a synthetic exact saved ACK and final receipt through the real loopback service, verifies cleanup, returns to the Activity and checks the existing report action. Wrong ACK, stale/new attempt, authority expiry and process loss must not restore collection authority. Synthetic acknowledgments do not establish a real account save.

No fresh rendered acceptance is claimed by this reference document; the final release receipt links the resulting screenshots and runtime checks.
