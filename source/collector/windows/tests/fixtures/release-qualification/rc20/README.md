# Exact RC20 Windows handoff qualification fixture

These are the exact Ed25519-signed update manifest and signature for the private
RC20 candidate. They do not open a public update channel or establish publication.

- Version/build: `2026.10.21-rc20` / `26102120`; MSI `1.0.20`, unsigned Test.
- MSI: 42285215 bytes, SHA256 `6c138c0c4b935908b8632f365e6f80178e1abb7e170aa59fbbab16a42b97dd0c`.
- Build source: `4f4da80aacf14accf07590a344fb8f38da537401`.
- Successful build run: `39699233385`; artifact `12392491187`.
- Artifact ZIP: 44792355 bytes, SHA256 `77c4306de4314ab465ad52a329fc11ffac8a27987682ad717044dd226d15b3be`.
- Existing publisher public key: `vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=`.

The workflow checks the exact successful build/artifact metadata, ZIP and extracted
MSI/candidate-receipt bytes before using these signatures. The production frozen
helper independently verifies the manifest, MSI and runtime guards. The prior
published RC19 installer is the upgrade baseline. All data is disposable runner
fixture data; no customer source or account Save is involved.
