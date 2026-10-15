# Signed rc19 qualification fixture

These are the exact signed manifest bytes and detached signature for the unsigned Windows TEST candidate MSI built from `d82bf65977a607026878019864e2e91a77bb5b1b`.

- Version/build: `2026.10.15-rc19` / `26101519`; MSI `1.0.19`.
- MSI: 41,989,466 bytes; SHA-256 `7fe9b10b9bc212e3f5f9591280a54b170a4b6762c5e78369acf5c9ebcabbba44`.
- Manifest: 581 bytes; SHA-256 `d6310cd713c782312a6ba7d9a4a918329562e01de4b280e4c36a319784645e70`.
- Signature: 89 bytes; SHA-256 `fdfacc39ca759db93a7498c397947e05a1d1c257f21f191b7233704487db2a0e`.

The handoff workflow downloads only Actions artifact `11983395801` from successful run `38754155786`, verifies its ZIP digest, and extracts the two fixed MSI and candidate-receipt members. It qualifies replacement of the released rc18 app with this rc19 candidate. The workflow needs `contents: read` and `actions: read`.

The fixture remains untrusted input until the normal manifest verifier checks the existing Ed25519 public key, exact signed bytes, Windows CPU/channel/build, and installer signature/hash/length. It introduces no test key or runtime bypass. Committing these public signature bytes does not publish the MSI or establish successful normal Windows Installer handoff.
