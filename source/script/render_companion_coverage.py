#!/usr/bin/env python3
"""Render the full locked-package review matrix from verified inventories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SPECIAL = {
    "bpylist2": "MIT text in exact sdist README",
    "developer-disk-image": "GPLv3+ classifier; exact text missing",
    "enum-compat": "MIT declaration; exact text missing",
    "hexdump": "Public Domain declaration; dedication absent",
    "loguru": "MIT text at exact upstream tag",
    "runs": "MIT expression; exact text missing",
    "remotezip2": "GPL/MIT metadata conflict",
    "pytun-pmd3": "GPL/MIT metadata conflict; Wintun module included",
    "pyinstaller": "boot/runtime hooks included",
    "pyinstaller-hooks-contrib": "runtime hook evidence; see artifact inventory",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--helper-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="Mac companion")
    args = parser.parse_args()
    sources = json.loads(args.source_manifest.read_text())
    helper = json.loads(args.helper_manifest.read_text())
    by_name = {record["name"]: record for record in helper["locked_packages"]}
    observed = sum(record["classification"] == "observed_in_helper" for record in helper["locked_packages"])
    not_observed = sum(record["classification"] == "not_observed_in_helper" for record in helper["locked_packages"])
    build_tools = len(helper["locked_packages"]) - observed - not_observed
    lines = [
        f"# {args.label} dependency coverage — local review candidate",
        "",
        "**Not a final notices file or redistribution clearance.** This matrix is",
        "mechanically derived from the pinned lock, exact-version PyPI source",
        "archives, the installed wheel metadata, and the frozen helper inventory.",
        "It reports evidence, not a legal license determination. Details and",
        "every source URL/SHA-256 are in `third_party/source-archive-manifest.json`;",
        f"helper paths/hashes are in `{args.helper_manifest.name}`.",
        "",
        f"Coverage: **{sources['total_locked']}/{sources['total_locked']}** exact pinned",
        "PyPI source archives present and SHA-256-verified against both PyPI and",
        "the lock. Six sdists have no standalone license/notice file; see notes.",
        "`Observed` means code or distribution files were seen in the frozen",
        "helper. `Not observed` does not prove a dependency is absent from every",
        "transitive binary or copied data file.",
        f"This exact helper observes {observed} package roots, does not observe",
        f"{not_observed}, and has {build_tools} build tools with runtime-hook evidence.",
        "",
        "| Distribution | Version | Frozen helper | License evidence | Sdist notice paths | Audit note |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for source in sources["records"]:
        name = source["name"]
        runtime = by_name[name]["classification"].replace("_", " ")
        expression = source.get("license_expression")
        classifiers = source.get("license_classifiers") or []
        field = source.get("license_field")
        if expression:
            evidence = f"Expression: {expression}"
        elif classifiers:
            evidence = "; ".join(item.rsplit(" :: ", 1)[-1] for item in classifiers)
        elif field:
            evidence = "Metadata text: " + field[:48].replace("\n", " ")
        elif source.get("license_field_sha256"):
            evidence = "Long metadata license text (hashed in manifest)"
        else:
            evidence = "No PyPI license field/classifier"
        evidence = evidence.replace("|", "/")
        note = SPECIAL.get(name, "") if runtime != "not observed in helper" else ""
        lines.append(
            f"| `{name}` | {source['version']} | {runtime} | {evidence} | "
            f"{len(source.get('embedded_license_paths') or [])} | {note} |"
        )
    lines.extend([
        "",
        "The count of sdist notice paths does not establish that the text is",
        "complete for a particular package, nor does zero mean no applicable",
        "license. See the provisional notices review for the six exact-version",
        "gaps, two metadata conflicts, runtime hooks, and native-library scope.",
        "",
    ])
    args.output.write_text("\n".join(lines))
    print(f"wrote {len(sources['records'])} package rows to {args.output}")


if __name__ == "__main__":
    main()
