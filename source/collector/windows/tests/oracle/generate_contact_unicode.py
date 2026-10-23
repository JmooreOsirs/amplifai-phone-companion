"""Reproduce and verify the compatibility tables from captured Mac properties.

Run with the supported Python 3.12 Unicode 15 baseline. Swift probe sources and
lossless property ranges are retained beside this file; no network is involved.
Pass --output to write regenerated patterns without changing production source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path


def pattern(values):
    ranges = []
    for value in sorted(values):
        if ranges and ranges[-1][1] + 1 == value:
            ranges[-1][1] = value
        else:
            ranges.append([value, value])

    def escape(value):
        return f"\\u{value:04x}" if value <= 0xffff else f"\\U{value:08x}"

    return "[" + "".join(escape(start) + ("-" + escape(end) if end != start else "")
                         for start, end in ranges) + "]"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if unicodedata.unidata_version != "15.0.0":
        raise RuntimeError("Regeneration requires Python 3.12 / Unicode 15.0.0")
    root = Path(__file__).resolve().parent
    provenance = json.loads((root / "provenance.json").read_text())
    for name, expected in provenance["files"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Oracle custody mismatch: " + name)
    captured = json.loads((root / "unicode-ranges.json").read_text())
    scalar, boundary = ({key: {value for start, end in ranges for value in range(start, end + 1)}
                         for key, ranges in captured[group].items()} for group in ("scalar", "boundary"))
    marks, extends = set(), set()
    for value in range(0x110000):
        category = unicodedata.category(chr(value))
        if category.startswith("M"):
            marks.add(value)
        if category in ("Mn", "Me"):
            extends.add(value)
    # Emoji modifiers join their base although Swift's scalar property omits
    # them. They extend the pictographic scan in the character-boundary rule.
    character_extend = scalar["extend"] | set(range(0x1f3fb, 0x1f400))
    values = {
        "WORD_ADDITIONS": {value for value in scalar["alpha"] if not chr(value).isalnum()},
        "EXTEND_ADDITIONS": character_extend - extends,
        "EXTEND_EXCLUDE": extends - scalar["extend"],
        "JOIN_ADDITIONS": scalar["joined"] - marks,
        "JOIN_EXCLUDE": marks - scalar["joined"],
        "PREPEND": scalar["prepend"],
        "PICTOGRAPHIC": boundary["pictographic"],
        "CONSONANT": boundary["consonant"],
        "LINKER": boundary["linker"],
        "CONJUNCT_EXTRA": boundary["conjunctExtend"] - character_extend,
        "CONJUNCT_EXCLUDE": character_extend - boundary["conjunctExtend"],
    }
    patterns = {key: pattern(points) for key, points in values.items()}
    sys.path.insert(0, str(root.parents[1]))
    import contact_workspace
    for name, expected in patterns.items():
        if getattr(contact_workspace, name).pattern != expected:
            actual = {value for value in range(0x110000)
                      if getattr(contact_workspace, name).fullmatch(chr(value))}
            if actual != values[name]:
                raise RuntimeError(f"Generated Unicode table differs: {name}; "
                                   f"missing={sorted(values[name] - actual)[:12]}, "
                                   f"extra={sorted(actual - values[name])[:12]}")
    if args.output:
        args.output.write_text(json.dumps(patterns, indent=2) + "\n")
    print(json.dumps({"status": "matched", "tables": len(patterns),
                      "unicodeVersion": unicodedata.unidata_version,
                      "authorityCommit": provenance["authorityCommit"]}))


if __name__ == "__main__":
    main()
