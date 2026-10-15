# Desktop update corresponding source

The authored AMPLIFai Phone companion is Copyright (C) 2026 OSIRS LLC and is
licensed under GPL-3.0-or-later. The accompanying LICENSE and COPYRIGHT retain
the license and warranty terms. THIRD-PARTY-NOTICES.txt retains upstream terms.

This release includes the bounded protocol journal fix and strict source-failure
diagnostics retained through partial success and local relaunch. Current facts
are included in the transfer hashes; previous-run history stays local and is
never attached to a new capture or Save. It retains compatible update discovery,
native idle and unsaved review gates, and the bounded running-app status service.
It preserves the
collection, review, explicit account Save and acknowledgment boundaries. The
source does not establish physical phone, customer account or hardware acceptance.

The matching release's source archive contains the exact committed authored
repository, dependency locks, build scripts and source input archives/notices.
Its release receipt identifies the commit, package/source checksums and matching
artifact inventory. The historical root README describes the inherited rc23/rc24
collector; the current desktop contract and source pins are in docs/.

Apple silicon uses source/script/build_portable_candidate.sh --isolated. Intel
uses the pinned dependency and Python source preparation followed by
source/script/build_macos_intel_candidate.py, as recorded in the Intel workflow.
Windows uses the pinned Windows requirements and candidate/MSI scripts in its
release workflow. Run each native build on its matching architecture and OS.
Build-source availability is distinct from reproducing upstream binary wheels.

The source offer includes the runtime dependencies of the matching package.
Existing native archives are retained only after their exact recorded hashes
match. Intel and Windows source archives additionally carry their own platform
runtime receipts and source inputs. The private website/backend is separate.

Developer ID signing, notarization and Sparkle publisher keys are not necessary
to study or build the source. Keys and credentials are not included. Local builds
must use their own identity and update channel; they cannot publish updates for
the existing signed application identity.
