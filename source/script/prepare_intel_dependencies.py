#!/usr/bin/env python3
"""Prepare the unchanged RC23 dependency versions for a native Intel Mac build.

The upstream cryptography 50.0.1 release has no Intel Mac wheel. Build that
exact locked source with pinned Rust and a private static OpenSSL, rather than
downgrading it or silently resolving newer dependencies. No Homebrew is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.parse
import urllib.request
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "collector/intel-dependency-inputs.json"
DEFAULT_LOCK = ROOT / "collector/portable-requirements.lock"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def lock_packages(text: str) -> dict[str, tuple[str, set[str]]]:
    result = {}
    for block in re.split(r"(?m)(?=^[A-Za-z0-9][\w.-]*==)", text):
        match = re.match(r"([\w.-]+)==([^\s\\]+)", block)
        if match:
            name, version = match.groups()
            hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", block))
            if name in result or not hashes:
                raise ValueError(f"Duplicate or unhashed locked requirement: {name}")
            result[name] = (version, hashes)
    if not result:
        raise ValueError("No hash-pinned requirements found")
    return result


def validate_manifest(manifest: dict, lock: Path) -> None:
    if manifest.get("schema") != 1 or manifest.get("target") != {
        "python": "3.12", "architecture": "x86_64", "deployment_target": "14.0"
    }:
        raise ValueError("Unsupported Intel dependency manifest")
    if digest(lock) != manifest["runtime_lock_sha256"]:
        raise ValueError("Portable requirements changed; re-audit Intel dependencies")
    expected = lock_packages(lock.read_text())
    actual = {}
    for item in manifest["runtime_wheels"] + manifest["runtime_sources"]:
        name = item["name"]
        if name in actual or name not in expected:
            raise ValueError(f"Unexpected or duplicate runtime input: {name}")
        version, hashes = expected[name]
        if item["version"] != version or item["sha256"] not in hashes:
            raise ValueError(f"Runtime input differs from original lock: {name}")
        actual[name] = version
    if actual.keys() != expected.keys():
        raise ValueError("Intel manifest does not cover every runtime requirement")
    if {x["name"] for x in manifest["runtime_sources"]} != {"cryptography", "hexdump"}:
        raise ValueError("Unreviewed source-build requirement")
    for item in (manifest["runtime_wheels"] + manifest["runtime_sources"] +
                 manifest["build_wheels"] + [manifest["openssl"]]):
        if (Path(item["filename"]).name != item["filename"] or
                not SHA256.fullmatch(item["sha256"])):
            raise ValueError("Invalid artifact name or checksum")
        url = urllib.parse.urlsplit(item["url"])
        if url.scheme != "https" or url.hostname not in {"files.pythonhosted.org", "github.com"}:
            raise ValueError("Unexpected dependency download origin")


def download_verified(item: dict, directory: Path) -> Path:
    """Never install/extract partial, replaced, or mismatched download bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / item["filename"]
    if destination.exists():
        if not destination.is_file() or digest(destination) != item["sha256"]:
            raise ValueError(f"Cached artifact checksum mismatch: {destination.name}")
        return destination
    temporary = destination.with_name(destination.name + ".partial")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "AMPLIFai-Intel-build/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as output:
            shutil.copyfileobj(response, output)
        if digest(temporary) != item["sha256"]:
            raise ValueError(f"Downloaded artifact checksum mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def extract_verified(archive: Path, destination: Path) -> Path:
    """Extract only inside a fresh directory, rejecting traversal and zip links."""
    destination.mkdir(parents=True, exist_ok=False)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(destination.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unsafe source archive member")
            bundle.extractall(destination)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")
    entries = list(destination.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else destination


def cryptography_cargo_manifest(source: Path) -> Path:
    """Follow the verified source's build manifest without allowing path escape."""
    project = tomllib.loads((source / "pyproject.toml").read_text())
    maturin = project["tool"]["maturin"]
    path = (source / maturin["manifest-path"]).resolve()
    if (not path.is_relative_to(source.resolve()) or not path.is_file() or
            maturin.get("locked") is not True or not (source / "Cargo.lock").is_file()):
        raise ValueError("Cryptography source lacks a locked, contained Cargo manifest")
    return path


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, text=True, check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    return result.stdout.strip()


def write_requirements(path: Path, items: list[dict]) -> None:
    path.write_text("".join(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}\n"
                            for item in sorted(items, key=lambda item: item["name"])))


def require_native_host() -> None:
    if (platform.system() != "Darwin" or platform.machine() != "x86_64" or
            platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 12)):
        raise ValueError("Build requires macOS x86_64 CPython 3.12; use the macos-15-intel runner")
    translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
                                capture_output=True, text=True)
    if translated.stdout.strip() == "1":
        raise ValueError("Use a native Intel runner, not Rosetta, for this qualification build")


def build(manifest: dict, output: Path, jobs: int) -> dict:
    require_native_host()
    source_inputs = output / "source-inputs"
    wheelhouse = output / "wheelhouse"
    build_wheels = output / "build-wheels"
    work = output / "work"
    work.mkdir()
    tool_version = run(["rustc", "--version"])
    if not tool_version.startswith(f"rustc {manifest['rust']['version']} "):
        raise ValueError(f"Expected Rust {manifest['rust']['version']}; received {tool_version}")
    cargo_version = run(["cargo", "--version"])
    if not cargo_version.startswith(f"cargo {manifest['rust']['version']} "):
        raise ValueError("Cargo and Rust must use the same pinned toolchain")
    python_env = work / "build-env"
    venv.EnvBuilder(with_pip=True).create(python_env)
    python = str(python_env / "bin/python")
    build_lock = source_inputs / "build-requirements.lock"
    write_requirements(build_lock, manifest["build_wheels"])
    run([python, "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--require-hashes",
         "--find-links", str(build_wheels), "-r", str(build_lock)])
    env = dict(os.environ)
    # Remove inherited discovery/compiler settings that could select Homebrew or
    # an unrelated OpenSSL/Rust target, then specify the complete local build.
    for name in list(env):
        if name.startswith(("OPENSSL_", "CARGO_", "RUST", "PYO3_", "PKG_CONFIG")) or name in {
            "CFLAGS", "CPPFLAGS", "LDFLAGS", "ARCHFLAGS", "CPATH", "LIBRARY_PATH", "DYLD_LIBRARY_PATH"
        }:
            env.pop(name)
    rustc = shutil.which("rustc")
    cargo = shutil.which("cargo")
    # Resolve rustup proxies to the pinned actual binaries before removing the
    # rustup environment; CI can supply RUSTUP_TOOLCHAIN as usual.
    if shutil.which("rustup"):
        rustc = run(["rustup", "which", "--toolchain", manifest["rust"]["version"], "rustc"])
        cargo = run(["rustup", "which", "--toolchain", manifest["rust"]["version"], "cargo"])
    env.update({"MACOSX_DEPLOYMENT_TARGET": "14.0", "ARCHFLAGS": "-arch x86_64",
                "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++", "CARGO_HOME": str(work / "cargo-home"),
                "CARGO_BUILD_TARGET": "x86_64-apple-darwin", "RUSTC": rustc, "CARGO": cargo,
                "PATH": str(Path(cargo).parent) + ":" + str(python_env / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
                "SOURCE_DATE_EPOCH": "1791763200"})
    openssl = manifest["openssl"]
    ssl_source = extract_verified(source_inputs / openssl["filename"], work / "openssl-source")
    ssl_prefix = work / "openssl"
    run(["/usr/bin/perl", "Configure", "darwin64-x86_64-cc", "no-shared", "no-tests",
         f"--prefix={ssl_prefix}", f"--openssldir={ssl_prefix}/ssl", "-mmacosx-version-min=14.0"], cwd=ssl_source, env=env)
    run(["/usr/bin/make", f"-j{jobs}"], cwd=ssl_source, env=env)
    run(["/usr/bin/make", "install_sw"], cwd=ssl_source, env=env)
    openssl_version = run([str(ssl_prefix / "bin/openssl"), "version"], env=env)
    if not openssl_version.startswith(f"OpenSSL {openssl['version']} "):
        raise ValueError("Unexpected locally built OpenSSL")
    shutil.copyfile(ssl_source / "LICENSE.txt", source_inputs / "OpenSSL-LICENSE.txt")
    env.update({"OPENSSL_DIR": str(ssl_prefix), "OPENSSL_STATIC": "1", "OPENSSL_NO_VENDOR": "1"})
    built = []
    for item in manifest["runtime_sources"]:
        source = extract_verified(source_inputs / item["filename"], work / (item["name"] + "-source"))
        if item["name"] == "cryptography":
            cargo_manifest = cryptography_cargo_manifest(source)
            vendor = source_inputs / "cargo-vendor"
            run([cargo, "vendor", "--locked", "--versioned-dirs", "--manifest-path", str(cargo_manifest), str(vendor)], env=env)
            shutil.copyfile(source / "Cargo.lock", source_inputs / "cryptography-Cargo.lock")
            cargo_config = ("[source.crates-io]\nreplace-with = \"vendored-sources\"\n"
                            "[source.vendored-sources]\ndirectory = " + json.dumps(str(vendor)) + "\n")
            (work / "cargo-home" / "config.toml").write_text(cargo_config)
            # Retain the full checked crate sources and bundled license files,
            # not just their resolved names, for rebuilding and source offers.
            (source_inputs / "cargo-vendor-README.txt").write_text(
                "Vendored from the hash-verified cryptography sdist Cargo.lock using Cargo " +
                manifest["rust"]["version"] + " --locked --versioned-dirs.\n"
                "The original per-crate licenses and .cargo-checksum.json files are retained.\n"
                "Configure source.crates-io.replace-with=vendored-sources and set the\n"
                "vendored-sources directory to this cargo-vendor directory, then build offline.\n")
            env["CARGO_NET_OFFLINE"] = "true"
            run([cargo, "fetch", "--locked", "--offline", "--manifest-path", str(cargo_manifest)], env=env)
        before = set(wheelhouse.glob("*.whl"))
        run([python, "-m", "pip", "--isolated", "wheel", "--no-index", "--no-deps", "--no-build-isolation",
             "--no-cache-dir", "--wheel-dir", str(wheelhouse), str(source)], env=env)
        additions = set(wheelhouse.glob("*.whl")) - before
        if len(additions) != 1:
            raise ValueError(f"Expected exactly one built wheel for {item['name']}")
        wheel = additions.pop()
        if not wheel.name.startswith(item["name"].replace("-", "_") + "-" + item["version"] + "-"):
            raise ValueError("Source build changed the locked package identity")
        built.append({"name": item["name"], "version": item["version"], "filename": wheel.name,
                      "sha256": digest(wheel), "source_sha256": item["sha256"]})
    runtime_items = manifest["runtime_wheels"] + built
    runtime_lock = output / "runtime-requirements.lock"
    write_requirements(runtime_lock, runtime_items)
    run([python, "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--require-hashes",
         "--find-links", str(wheelhouse), "-r", str(runtime_lock)])
    run([python, "-m", "pip", "check"])
    run([python, "-c", "import cryptography; from cryptography.hazmat.backends.openssl.backend import backend; "
         "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; "
         "assert cryptography.__version__ == '50.0.1'; "
         "assert backend.openssl_version_text().startswith('OpenSSL 3.5.9 '); "
         "a=AESGCM(bytes(32)); c=a.encrypt(bytes(12), b'intel-static-build-check', None); "
         "assert a.decrypt(bytes(12),c,None)==b'intel-static-build-check'; "
         "print(cryptography.__version__, backend.openssl_version_text())"])
    rust_extensions = list(python_env.glob("lib/python3.12/site-packages/cryptography/hazmat/bindings/_rust*.so"))
    if len(rust_extensions) != 1:
        raise ValueError("Missing or ambiguous cryptography native extension")
    architecture = run(["/usr/bin/lipo", "-archs", str(rust_extensions[0])])
    if architecture != "x86_64":
        raise ValueError("Cryptography was not compiled for Intel")
    links = run(["/usr/bin/otool", "-L", str(rust_extensions[0])])
    if any(x in links for x in ("libssl", "libcrypto", "/opt/homebrew", "/usr/local/", "/opt/local/")):
        raise ValueError("Cryptography unexpectedly depends on external OpenSSL/package-manager libraries")
    return {"status": "built-and-validated", "rustc": tool_version, "cargo": cargo_version,
            "openssl": openssl_version, "built_wheels": built, "runtime_lock_sha256": digest(runtime_lock),
            "cryptography_architecture": architecture, "cryptography_linkage": links,
            "runtime_requirement_count": len(runtime_items),
            "cargo_lock_sha256": digest(source_inputs / "cryptography-Cargo.lock"),
            "vendored_rust_crates": len(list((source_inputs / "cargo-vendor").iterdir())),
            "rust_source_offer": "source-inputs/cargo-vendor",
            "minimum_os_note": "14.0 deployment target; actual Intel macOS 14 and physical iPhone qualification still required"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runtime-lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--download-only", action="store_true", help="Verify/download inputs on any host; do not build or claim runtime qualification")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error("--jobs must be between 1 and 32")
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest, args.runtime_lock)
    if not args.download_only:
        require_native_host()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Output directory must be empty; use a new build directory")
    source_inputs = output / "source-inputs"
    source_inputs.mkdir()
    shutil.copyfile(args.manifest, source_inputs / "intel-dependency-inputs.json")
    shutil.copyfile(args.runtime_lock, source_inputs / "original-runtime-requirements.lock")
    artifacts = ([(item, output / "wheelhouse") for item in manifest["runtime_wheels"]] +
                 [(item, output / "build-wheels") for item in manifest["build_wheels"]] +
                 [(item, source_inputs) for item in manifest["runtime_sources"] + [manifest["openssl"]]])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda pair: download_verified(*pair), artifacts))
    receipt = {"schema": 1, "manifest_sha256": digest(args.manifest),
               "original_runtime_lock_sha256": digest(args.runtime_lock), "target": manifest["target"],
               "host": {"system": platform.system(), "machine": platform.machine(), "macos": platform.mac_ver()[0],
                        "python": platform.python_version()},
               "verified_inputs": [{"filename": item["filename"], "url": item["url"], "sha256": item["sha256"]} for item, _ in artifacts],
               "rust_toolchain": manifest["rust"], "openssl_source": manifest["openssl"], "status": "download-only"}
    if not args.download_only:
        receipt.update(build(manifest, output, args.jobs))
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt": str(output / "receipt.json")}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stdout:
            print(exc.stdout, file=sys.stderr)
        print(f"Intel dependency preparation failed: {exc}", file=sys.stderr)
        sys.exit(1)
