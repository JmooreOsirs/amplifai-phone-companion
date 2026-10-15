AMPLIFai RC25 message-transfer fix — source patch for Jeff

BASE: 2f773fff5208c5da6274029b6db92af213ca70aa (Mac RC25 / build 26101425).
Use the matching canonical release source. Public main is a historical source snapshot; do not replace the current collector with an older fork.

REVIEW BRANCH AND QUICK IMPORT
This PR is based on our shared published RC23 source baseline. Its original backup_stream.py is byte-identical to the RC25 target (SHA256 028420116f37d47cb606267a4fb3c5ed39957c2a4a7991d04f6542de55b1ce3a). The PR changes only the streaming adapter, adds a self-contained test, and includes this handoff. It imports no private canonical source tree or history.

From the canonical RC25 release branch, import this one fix commit:
  git fetch https://github.com/Osirs-io/amplifai-phone-companion.git codex/rc25-message-transfer-fix
  git cherry-pick FETCH_HEAD

Alternatively apply the standalone patch below. Do not merge the whole historical baseline into the canonical release branch.

WHAT IS PROVEN
The exact RC25 streaming adapter creates an empty protocol placeholder for a filtered sms.db-journal upload, but leaves it unregistered for SDK cleanup. When the final manifest has no journal and the encrypted main needs the smaller-file compatibility check, the leftover placeholder causes selected_payload_length / sidecar_present. The same fictional encrypted source succeeds before the journal upload and after removing only that known host-created stub. The installed helper matches the published source. The fixture condition was also reproduced with the installed validation worker and Python/SQLite runtime.

LIMIT
This is a reproduced shared failure condition, not proof of the exact trigger in the October 15 owner run: that run's optional-source details were not retained. Neither a successful older-backup parse nor these fixtures establish a fresh physical-phone/account/report acceptance result.

APPLY FROM THE MATCHING REPOSITORY ROOT
Use the existing release-locked Python environment. Copy this kit's patch into a location outside the source tree, then run:

  git switch -c fix/mac-message-placeholder-cleanup 2f773fff5208c5da6274029b6db92af213ca70aa
  git apply --check /path/to/fix-filtered-journal-placeholders.patch
  git apply /path/to/fix-filtered-journal-placeholders.patch
  PYTHONPATH=source/collector python -m unittest discover -s source/collector/tests -p 'test_filtered_journal_cleanup.py' -v
  PYTHONPATH=source/collector python -m unittest discover -s source/collector/tests -p 'test_backup_stream.py' -v

The patch includes the self-contained regression test. Check the diff before committing. Preserve upstream source notices/licenses.

VALIDATION COMPLETED HERE
Original behavioral regression failed with selected_payload_length. All 12 new regression tests and 28 canonical streaming tests passed after the patch. An independent reviewer reran both suites. git apply --check passed against the exact RC25 source.

PATCH SAFETY
Only three canonical source-journal IDs are eligible for tracking, with a maximum of 32 aliases. Remove only proven host-created empty regular files with the expected identity, after the Backup request completes successfully. Preserve protocol readback, selected files, actual/nonempty/replaced/untracked sidecars, manifest sidecar checks, bounded memory and existing encryption/digest/copy/integrity checks. Handshake success must not arm cleanup. Chained moves, copy/remove/replacement and alias limits have regressions.

REQUIRED FOLLOW-UP
The patch does not implement durable optional-source diagnostics. Retain safe allowlisted per-source failure details outside disposable sessions through partial success and relaunch, and clearly distinguish a failed message read from valid zero activity. Do not retain phone databases, raw manifests, personal records, paths, credentials or message content in support reports.

RELEASE ACCEPTANCE
Rebuild the helper and Mac app using the existing release process; Developer ID sign, notarize and staple the new app/installer, and bind the corresponding source and hashes. Test the exact new signed build with a fresh physical iPhone containing known SMS and iMessage activity. Verify local review, Save/ACK, Finalize, report totals, and sign-out/sign-in persistence. Retain a safe source reason if any category is unavailable. Only then publish the qualified update. No key transfer or new Apple membership is needed.

No production code/configuration, installed app, owner records or phone were changed by this patch preparation. This kit contains no owner backup or diagnostic data; all included reproduction evidence uses fictional fixtures.
