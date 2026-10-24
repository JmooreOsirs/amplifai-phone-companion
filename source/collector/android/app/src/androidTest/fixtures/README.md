# Updater test assets

The secret-free hosted producer prepares these ignored assets before building instrumentation. They are never packaged in the production APK or tracked in Git.

- `../assets/rc7-signed.apk`: original public Android RC7, 108317 bytes, SHA-256 `e4ce5ad5397fa82449f10e74f86b4070ae20fc9c1650329fc6472b842676b777`. Its signed feed is tracked beside it. Source: https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/android-2026.10.08-rc7
- `../assets/rc8-debug.apk`: manifest-only archive generated from `wrong-signer/AndroidManifest.xml` with build-tools 36.0.0. A temporary synthetic test identity signs it; the producer verifies that it is not the publisher identity, records its exact hash, then deletes the test key. No executable code, production key, user data or network request is included. It is never installed.

These old versions are deliberate archive inputs to `verifyArchive` with a synthetic lower installed version. Negative cases assert the exact identity, integrity or signer failure reason. Callback state tests derive future targets from the actual installed app. Dedicated `updater_install_auto/manual` cases retain their explicitly required historical RC8 installation and are not part of current release qualification.
