# Signed rc18 qualification fixture

These are the exact signed manifest bytes and detached signature for the unsigned Windows TEST candidate MSI built from `6075bdeb47becfbbadad3c07e44cb7cfb3fdd6af`.

- Version/build: `2026.10.14-rc18` / `26101418`; MSI `1.0.18`.
- MSI: 41,928,026 bytes; SHA-256 `295b9940988e21dd71069f7dfe7c6407188c5c0213d61233af8d773a59aef1c9`.
- Manifest: 581 bytes; SHA-256 `cea90ce923ac363562835eddd184e4ab4a6ed47db9785311bb97fd7a077fd42d`.
- Signature: 89 bytes; SHA-256 `75bb721232f6fecc3e0fa2149768a6bf64411edae1a6a3cfe148b4b81978f18e`.

The handoff workflow downloads only Actions artifact `11929148386` from successful run `38638633877`, verifies its ZIP digest, and extracts the two fixed MSI and candidate-receipt members. The workflow needs `contents: read` and `actions: read`; it does not read the unpublished draft release.

The fixture remains untrusted input until the normal manifest verifier checks the existing Ed25519 public key, exact signed bytes, Windows CPU/channel/build, and installer signature/hash/length. It introduces no test key or runtime bypass. Committing these public signature bytes does not publish the MSI or establish successful normal Windows Installer handoff.
