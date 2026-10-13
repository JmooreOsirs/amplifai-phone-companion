# Import the Intel fix with one command

This pull request contains the Intel fix and a small ownership/link follow-up. Its comparison base is the published RC23 authored-source baseline. Do not merge the entire fork history into an independently maintained collector.

## Import

Start in the current canonical companion checkout, on the branch containing the RC23 collector or its newer replacement. On 2026-10-12 the public main branch was still RC6; it is not the matching target. The commands below assume the published source/collector layout. If the private checkout uses collector/ at its root, map the paths explicitly rather than replacing its tree.

Run with a clean working tree:

```sh
git switch -c integrate-intel-mac
git fetch https://github.com/Osirs-io/amplifai-phone-companion.git codex/intel-rc23-ready
git cherry-pick 81ecfb5..FETCH_HEAD
```

This picks the focused Intel fix and ownership follow-up, excluding the source-baseline import. It also omits the unrelated root README replacement. If newer canonical changes conflict, resolve those specific hunks while preserving the newer collector; do not accept the fork's whole files to force a merge. The included patch has been checked against the published RC23 source layout.

Review the changes and merge this integration branch through the canonical repository's normal process.

## Build after import

The native app and collector runtime files are byte-identical to the successfully tested code at 5756202162db8415e37b45f3c38bcca22930bcf6. The ownership follow-up changes only repository links, documentation and the workflow output-owner guard; the builder changes only its embedded source-notice URL. INTEL-VERIFICATION.md links the native Intel run and exact built app.

The workflow output setting makes builds usable after import: in GitHub Actions, choose **Intel Mac build and qualification**, choose **Run workflow**, and enable **Keep the unsigned build as a draft GitHub release**. When running outside our shared OSIRS fork, this creates a draft containing the app, corresponding source and receipts. It does not publish the customer release or change the canonical updater. Without that option the build still tests, but does not retain its outputs. Automatic push builds remain limited to the original development branch; main uses this manual trigger unless you deliberately change that policy.

This uses the standard Intel runner. For a public GitHub repository, standard Actions execution is free; private repositories should check their included Actions allowance before running.

## Finish the release

1. Use the existing authorized Developer ID process to sign/notarize/staple the Intel app and DMG; verify a normal downloaded installation and regenerate final checksums.
2. Enable the website's Intel installer and setup/pairing path. Keep ARM-only updates blocked for Intel until a compatible signed feed exists. See INTEL-MAC-HANDOFF.md for the exact integration points.
3. Qualify a real Intel Mac and iPhone through review, account Save acknowledgement, Finalize, report and sign-back-in persistence. Test macOS 14 explicitly before calling that version verified.

The import builds Intel support; merging alone does not sign the app, enable the website or establish real-phone/account qualification.
