"""Mac RC35 ContactWorkspace semantics over one complete verified snapshot.

Grouping is a name clue, never an identity merge or selection decision. Unicode
compatibility is pinned to the en_US Foundation oracle in
tests/fixtures/mac-contact-groups.json; locale-specific Turkic folding is not
provided by Python's locale-independent casefold.
"""
from __future__ import annotations

from copy import deepcopy
import re
import sys
import unicodedata

GROUPS = ("All", "Main", "Review")
PAGE_SIZE = 200
ORGANIZATION_MARKERS = frozenset(("llc", "pllc", "inc", "corp", "ltd", "company", "foundation", "association"))
# Foundation includes U+200B and excludes Python's U+001C...U+001F trim characters.
WHITESPACE = "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u2028\u2029\u202f\u205f\u3000"

# UnicodeScalar.isAlphabetic includes Other_Alphabetic marks omitted by Python's
# isalnum. These property additions also cover newer Swift Unicode assignments.
# Generated from the actual Swift oracle relative to supported Python 3.12 UCD 15.
WORD_ADDITIONS = re.compile("["
    r"\u0345\u0363-\u036f\u05b0-\u05bd\u05bf\u05c1-\u05c2\u05c4-\u05c5\u05c7\u0610-\u061a\u064b-\u0657"
    r"\u0659-\u065f\u0670\u06d6-\u06dc\u06e1-\u06e4\u06e7-\u06e8\u06ed\u0711\u0730-\u073f\u07a6-\u07b0"
    r"\u0816-\u0817\u081b-\u0823\u0825-\u0827\u0829-\u082c\u088f\u0897\u08d4-\u08df\u08e3-\u08e9"
    r"\u08f0-\u0903\u093a-\u093b\u093e-\u094c\u094e-\u094f\u0955-\u0957\u0962-\u0963\u0981-\u0983"
    r"\u09be-\u09c4\u09c7-\u09c8\u09cb-\u09cc\u09d7\u09e2-\u09e3\u0a01-\u0a03\u0a3e-\u0a42\u0a47-\u0a48"
    r"\u0a4b-\u0a4c\u0a51\u0a70-\u0a71\u0a75\u0a81-\u0a83\u0abe-\u0ac5\u0ac7-\u0ac9\u0acb-\u0acc"
    r"\u0ae2-\u0ae3\u0afa-\u0afc\u0b01-\u0b03\u0b3e-\u0b44\u0b47-\u0b48\u0b4b-\u0b4c\u0b56-\u0b57"
    r"\u0b62-\u0b63\u0b82\u0bbe-\u0bc2\u0bc6-\u0bc8\u0bca-\u0bcc\u0bd7\u0c00-\u0c04\u0c3e-\u0c44"
    r"\u0c46-\u0c48\u0c4a-\u0c4c\u0c55-\u0c56\u0c5c\u0c62-\u0c63\u0c81-\u0c83\u0cbe-\u0cc4\u0cc6-\u0cc8"
    r"\u0cca-\u0ccc\u0cd5-\u0cd6\u0cdc\u0ce2-\u0ce3\u0cf3\u0d00-\u0d03\u0d3e-\u0d44\u0d46-\u0d48"
    r"\u0d4a-\u0d4c\u0d57\u0d62-\u0d63\u0d81-\u0d83\u0dcf-\u0dd4\u0dd6\u0dd8-\u0ddf\u0df2-\u0df3\u0e31"
    r"\u0e34-\u0e3a\u0e4d\u0eb1\u0eb4-\u0eb9\u0ebb-\u0ebc\u0ecd\u0f71-\u0f83\u0f8d-\u0f97\u0f99-\u0fbc"
    r"\u102b-\u1036\u1038\u103b-\u103e\u1056-\u1059\u105e-\u1060\u1062-\u1064\u1067-\u106d\u1071-\u1074"
    r"\u1082-\u108d\u108f\u109a-\u109d\u1712-\u1713\u1732-\u1733\u1752-\u1753\u1772-\u1773\u17b6-\u17c8"
    r"\u1885-\u1886\u18a9\u1920-\u192b\u1930-\u1938\u1a17-\u1a1b\u1a55-\u1a5e\u1a61-\u1a74\u1abf-\u1ac0"
    r"\u1acc-\u1ace\u1b00-\u1b04\u1b35-\u1b43\u1b80-\u1b82\u1ba1-\u1ba9\u1bac-\u1bad\u1be7-\u1bf1"
    r"\u1c24-\u1c36\u1c89-\u1c8a\u1dd3-\u1df4\u24b6-\u24e9\u2de0-\u2dff\ua674-\ua67b\ua69e-\ua69f"
    r"\ua7cb-\ua7cf\ua7d2\ua7d4\ua7da-\ua7dc\ua7f1\ua802\ua80b\ua823-\ua827\ua880-\ua881\ua8b4-\ua8c3"
    r"\ua8c5\ua8ff\ua926-\ua92a\ua947-\ua952\ua980-\ua983\ua9b4-\ua9bf\ua9e5\uaa29-\uaa36\uaa43"
    r"\uaa4c-\uaa4d\uaa7b-\uaa7d\uaab0\uaab2-\uaab4\uaab7-\uaab8\uaabe\uaaeb-\uaaef\uaaf5\uabe3-\uabea"
    r"\uf882\uf89a-\uf89e\uf8a2-\uf8a7\uf8b8\uf8c1-\uf8d6\ufb1e\U00010376-\U0001037a\U000105c0-\U000105f3"
    r"\U00010940-\U00010959\U00010a01-\U00010a03\U00010a05-\U00010a06\U00010a0c-\U00010a0f"
    r"\U00010d24-\U00010d27\U00010d4a-\U00010d65\U00010d69\U00010d6f-\U00010d85\U00010eab-\U00010eac"
    r"\U00010ec2-\U00010ec7\U00010efa-\U00010efc\U00011000-\U00011002\U00011038-\U00011045"
    r"\U00011073-\U00011074\U00011080-\U00011082\U000110b0-\U000110b8\U000110c2\U00011100-\U00011102"
    r"\U00011127-\U00011132\U00011145-\U00011146\U00011180-\U00011182\U000111b3-\U000111bf"
    r"\U000111ce-\U000111cf\U0001122c-\U00011234\U00011237\U0001123e\U00011241\U000112df-\U000112e8"
    r"\U00011300-\U00011303\U0001133e-\U00011344\U00011347-\U00011348\U0001134b-\U0001134c\U00011357"
    r"\U00011362-\U00011363\U00011380-\U00011389\U0001138b\U0001138e\U00011390-\U000113b5"
    r"\U000113b7-\U000113c0\U000113c2\U000113c5\U000113c7-\U000113ca\U000113cc-\U000113cd\U000113d1"
    r"\U000113d3\U00011435-\U00011441\U00011443-\U00011445\U000114b0-\U000114c1\U000115af-\U000115b5"
    r"\U000115b8-\U000115be\U000115dc-\U000115dd\U00011630-\U0001163e\U00011640\U000116ab-\U000116b5"
    r"\U0001171d-\U0001172a\U0001182c-\U00011838\U00011930-\U00011935\U00011937-\U00011938"
    r"\U0001193b-\U0001193c\U00011940\U00011942\U000119d1-\U000119d7\U000119da-\U000119df\U000119e4"
    r"\U00011a01-\U00011a0a\U00011a35-\U00011a39\U00011a3b-\U00011a3e\U00011a51-\U00011a5b"
    r"\U00011a8a-\U00011a97\U00011b60-\U00011b67\U00011bc0-\U00011be0\U00011c2f-\U00011c36"
    r"\U00011c38-\U00011c3e\U00011c92-\U00011ca7\U00011ca9-\U00011cb6\U00011d31-\U00011d36\U00011d3a"
    r"\U00011d3c-\U00011d3d\U00011d3f-\U00011d41\U00011d43\U00011d47\U00011d8a-\U00011d8e"
    r"\U00011d90-\U00011d91\U00011d93-\U00011d96\U00011db0-\U00011ddb\U00011ef3-\U00011ef6"
    r"\U00011f00-\U00011f01\U00011f03\U00011f34-\U00011f3a\U00011f3e-\U00011f40\U00013460-\U000143fa"
    r"\U00016100-\U0001612e\U00016d40-\U00016d6c\U00016ea0-\U00016eb8\U00016ebb-\U00016ed3\U00016f4f"
    r"\U00016f51-\U00016f87\U00016f8f-\U00016f92\U00016ff0-\U00016ff6\U000187f8-\U000187ff\U00018cff"
    r"\U00018d09-\U00018d1e\U00018d80-\U00018df2\U0001bc9e\U0001e000-\U0001e006\U0001e008-\U0001e018"
    r"\U0001e01b-\U0001e021\U0001e023-\U0001e024\U0001e026-\U0001e02a\U0001e08f\U0001e5d0-\U0001e5ed"
    r"\U0001e5f0\U0001e6c0-\U0001e6de\U0001e6e0-\U0001e6f5\U0001e6fe-\U0001e6ff\U0001e947"
    r"\U0001f130-\U0001f149\U0001f150-\U0001f169\U0001f170-\U0001f189\U0002b73a-\U0002b73f"
    r"\U0002cea2-\U0002cead\U0002ebf0-\U0002ee5d\U000323b0-\U00033479"
    "]")
EXTEND_ADDITIONS = re.compile("["
    r"\u0897\u09be\u09d7\u0b3e\u0b57\u0bbe\u0bd7\u0cc0\u0cc2\u0cc7-\u0cc8\u0cca-\u0ccb\u0cd5-\u0cd6\u0d3e"
    r"\u0d57\u0dcf\u0ddf\u1715\u1734\u1acf-\u1add\u1ae0-\u1aeb\u1b35\u1b3b\u1b3d\u1b43-\u1b44\u1baa"
    r"\u1bf2-\u1bf3\u200c\u302e-\u302f\ua953\ua9c0\uf870-\uf87f\uf884-\uf899\uf89f\uff9e-\uff9f"
    r"\U00010d69-\U00010d6d\U00010efa-\U00010efc\U000111c0\U00011235\U0001133e\U0001134d\U00011357"
    r"\U000113b8\U000113bb-\U000113c0\U000113c2\U000113c5\U000113c7-\U000113c9\U000113ce-\U000113d0"
    r"\U000113d2\U000113e1-\U000113e2\U000114b0\U000114bd\U000115af\U000116b6\U00011930\U0001193d"
    r"\U00011b60\U00011b62-\U00011b64\U00011b66\U00011f41\U00011f5a\U0001611e-\U00016129"
    r"\U0001612d-\U0001612f\U00016ff0-\U00016ff1\U0001d165-\U0001d166\U0001d16d-\U0001d172"
    r"\U0001e5ee-\U0001e5ef\U0001e6e3\U0001e6e6\U0001e6ee-\U0001e6ef\U0001e6f5\U0001f3fb-\U0001f3ff"
    r"\U000e0020-\U000e007f"
    "]")
# UCD 15 calls this Mn; the captured Mac oracle treats it as a spacing mark.
EXTEND_EXCLUDE = re.compile(r"[\U0001171e]")
JOIN_ADDITIONS = re.compile("["
    r"\u0897\u0e33\u0eb3\u1acf-\u1add\u1ae0-\u1aeb\u200c-\u200d\uff9e-\uff9f\U00010d69-\U00010d6d"
    r"\U00010efa-\U00010efc\U000113b8-\U000113c0\U000113c2\U000113c5\U000113c7-\U000113ca"
    r"\U000113cc-\U000113d0\U000113d2\U000113e1-\U000113e2\U00011b60-\U00011b67\U00011f5a"
    r"\U0001611e-\U0001612f\U0001e5ee-\U0001e5ef\U0001e6e3\U0001e6e6\U0001e6ee-\U0001e6ef\U0001e6f5"
    r"\U0001f3fb-\U0001f3ff\U000e0020-\U000e007f"
    "]")
PREPEND = re.compile("["
    r"\u0600-\u0605\u06dd\u070f\u0890-\u0891\u08e2\U000110bd\U000110cd"
    "]")

PICTOGRAPHIC = re.compile("["
    r"\u00a9\u00ae\u203c\u2049\u2122\u2139\u2194-\u2199\u21a9-\u21aa\u231a-\u231b\u2328\u23cf\u23e9-\u23f3"
    r"\u23f8-\u23fa\u24c2\u25aa-\u25ab\u25b6\u25c0\u25fb-\u25fe\u2600-\u2604\u260e\u2611\u2614-\u2615"
    r"\u2618\u261d\u2620\u2622-\u2623\u2626\u262a\u262e-\u262f\u2638-\u263a\u2640\u2642\u2648-\u2653"
    r"\u265f-\u2660\u2663\u2665-\u2666\u2668\u267b\u267e-\u267f\u2692-\u2697\u2699\u269b-\u269c"
    r"\u26a0-\u26a1\u26a7\u26aa-\u26ab\u26b0-\u26b1\u26bd-\u26be\u26c4-\u26c5\u26c8\u26ce-\u26cf\u26d1"
    r"\u26d3-\u26d4\u26e9-\u26ea\u26f0-\u26f5\u26f7-\u26fa\u26fd\u2702\u2705\u2708-\u270d\u270f\u2712"
    r"\u2714\u2716\u271d\u2721\u2728\u2733-\u2734\u2744\u2747\u274c\u274e\u2753-\u2755\u2757\u2763-\u2764"
    r"\u2795-\u2797\u27a1\u27b0\u27bf\u2934-\u2935\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55\u3030\u303d\u3297"
    r"\u3299\U0001f004\U0001f02c-\U0001f02f\U0001f094-\U0001f09f\U0001f0af-\U0001f0b0\U0001f0c0"
    r"\U0001f0cf-\U0001f0d0\U0001f0f6-\U0001f0ff\U0001f170-\U0001f171\U0001f17e-\U0001f17f\U0001f18e"
    r"\U0001f191-\U0001f19a\U0001f1ae-\U0001f1e5\U0001f201-\U0001f20f\U0001f21a\U0001f22f"
    r"\U0001f232-\U0001f23a\U0001f23c-\U0001f23f\U0001f249-\U0001f25f\U0001f266-\U0001f321"
    r"\U0001f324-\U0001f393\U0001f396-\U0001f397\U0001f399-\U0001f39b\U0001f39e-\U0001f3f0"
    r"\U0001f3f3-\U0001f3f5\U0001f3f7-\U0001f3fa\U0001f400-\U0001f4fd\U0001f4ff-\U0001f53d"
    r"\U0001f549-\U0001f54e\U0001f550-\U0001f567\U0001f56f-\U0001f570\U0001f573-\U0001f57a\U0001f587"
    r"\U0001f58a-\U0001f58d\U0001f590\U0001f595-\U0001f596\U0001f5a4-\U0001f5a5\U0001f5a8"
    r"\U0001f5b1-\U0001f5b2\U0001f5bc\U0001f5c2-\U0001f5c4\U0001f5d1-\U0001f5d3\U0001f5dc-\U0001f5de"
    r"\U0001f5e1\U0001f5e3\U0001f5e8\U0001f5ef\U0001f5f3\U0001f5fa-\U0001f64f\U0001f680-\U0001f6c5"
    r"\U0001f6cb-\U0001f6d2\U0001f6d5-\U0001f6e5\U0001f6e9\U0001f6eb-\U0001f6f0\U0001f6f3-\U0001f6ff"
    r"\U0001f7da-\U0001f7ff\U0001f80c-\U0001f80f\U0001f848-\U0001f84f\U0001f85a-\U0001f85f"
    r"\U0001f888-\U0001f88f\U0001f8ae-\U0001f8af\U0001f8bc-\U0001f8bf\U0001f8c2-\U0001f8cf"
    r"\U0001f8d9-\U0001f8ff\U0001f90c-\U0001f93a\U0001f93c-\U0001f945\U0001f947-\U0001f9ff"
    r"\U0001fa58-\U0001fa5f\U0001fa6e-\U0001faff\U0001fc00-\U0001fffd"
    "]")
CONSONANT = re.compile("["
    r"\u0915-\u0939\u0958-\u095f\u0978-\u097f\u0995-\u09a8\u09aa-\u09b0\u09b2\u09b6-\u09b9\u09dc-\u09dd"
    r"\u09df\u09f0-\u09f1\u0a95-\u0aa8\u0aaa-\u0ab0\u0ab2-\u0ab3\u0ab5-\u0ab9\u0af9\u0b15-\u0b28"
    r"\u0b2a-\u0b30\u0b32-\u0b33\u0b35-\u0b39\u0b5c-\u0b5d\u0b5f\u0b71\u0c15-\u0c28\u0c2a-\u0c39"
    r"\u0c58-\u0c5a\u0d15-\u0d3a\u1000-\u102a\u103f\u1050-\u1055\u105a-\u105d\u1061\u1065-\u1066"
    r"\u106e-\u1070\u1075-\u1081\u108e\u1780-\u17b3\u1a20-\u1a54\u1b0b-\u1b0c\u1b13-\u1b33\u1b45-\u1b4c"
    r"\u1b83-\u1ba0\u1bae-\u1baf\u1bbb-\u1bbd\ua989-\ua98b\ua98f-\ua9b2\ua9e0-\ua9e4\ua9e7-\ua9ef"
    r"\ua9fa-\ua9fe\uaa60-\uaa6f\uaa71-\uaa73\uaa7a\uaa7e-\uaa7f\uaae0-\uaaea\uabc0-\uabda\U00010a00"
    r"\U00010a10-\U00010a13\U00010a15-\U00010a17\U00010a19-\U00010a35\U00011103-\U00011126\U00011144"
    r"\U00011147\U00011380-\U00011389\U0001138b\U0001138e\U00011390-\U000113b5\U00011900-\U00011906"
    r"\U00011909\U0001190c-\U00011913\U00011915-\U00011916\U00011918-\U0001192f\U00011a00"
    r"\U00011a0b-\U00011a32\U00011a50\U00011a5c-\U00011a83\U00011f04-\U00011f10\U00011f12-\U00011f33"
    "]")
LINKER = re.compile("["
    r"\u094d\u09cd\u0acd\u0b4d\u0c4d\u0d4d"
    "]")
CONJUNCT_EXTRA = re.compile("["
    r"\u200d"
    "]")
CONJUNCT_EXCLUDE = re.compile("["
    r"\u200c\uf870-\uf87f\uf884-\uf899\uf89f"
    "]")
JOIN_EXCLUDE = re.compile("["
    r"\u102b-\u102c\u1038\u1062-\u1064\u1067-\u106d\u1083\u1087-\u108c\u108f\u109a-\u109c\u1a61"
    r"\u1a63-\u1a64\uaa7b\uaa7d\U00011720-\U00011721"
    "]")


def _extends(character: str) -> bool:
    return ((unicodedata.category(character) in ("Mn", "Me") and not EXTEND_EXCLUDE.fullmatch(character))
            or bool(EXTEND_ADDITIONS.fullmatch(character)))


def _hangul_class(character: str) -> str:
    value = ord(character)
    if 0x1100 <= value <= 0x115F or 0xA960 <= value <= 0xA97C:
        return "L"
    if 0x1160 <= value <= 0x11A7 or 0xD7B0 <= value <= 0xD7C6:
        return "V"
    if 0x11A8 <= value <= 0x11FF or 0xD7CB <= value <= 0xD7FB:
        return "T"
    if 0xAC00 <= value <= 0xD7A3:
        return "LVT" if (value - 0xAC00) % 28 else "LV"
    return ""


def _joins_previous(value: str, start: int, index: int) -> bool:
    previous, following = value[index - 1:index + 1]
    if previous == "\r" and following == "\n":
        return True
    for character in (previous, following):
        if (unicodedata.category(character) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                and not JOIN_ADDITIONS.fullmatch(character) and not PREPEND.fullmatch(character)):
            return False
    if ((unicodedata.category(following).startswith("M") and not JOIN_EXCLUDE.fullmatch(following))
            or JOIN_ADDITIONS.fullmatch(following) or PREPEND.fullmatch(previous)):
        return True
    left, right = _hangul_class(previous), _hangul_class(following)
    if ((left == "L" and right in ("L", "V", "LV", "LVT"))
            or (left in ("LV", "V") and right in ("V", "T"))
            or (left in ("LVT", "T") and right == "T")):
        return True
    if 0x1F1E6 <= ord(previous) <= 0x1F1FF and 0x1F1E6 <= ord(following) <= 0x1F1FF:
        return (index - start) % 2 == 1
    if previous == "\u200d" and PICTOGRAPHIC.fullmatch(following):
        scan = index - 2
        while scan >= start and _extends(value[scan]):
            scan -= 1
        if scan >= start and PICTOGRAPHIC.fullmatch(value[scan]):
            return True
    if CONSONANT.fullmatch(following):
        scan, has_linker = index - 1, False
        while scan >= start and ((_extends(value[scan]) and not CONJUNCT_EXCLUDE.fullmatch(value[scan]))
                                 or CONJUNCT_EXTRA.fullmatch(value[scan])):
            has_linker = has_linker or bool(LINKER.fullmatch(value[scan]))
            scan -= 1
        return has_linker and scan >= start and bool(CONSONANT.fullmatch(value[scan]))
    return False


def _character_spans(value: str):
    index = 0
    while index < len(value):
        start = index
        index += 1
        while index < len(value) and _joins_previous(value, start, index):
            index += 1
        yield start, index


def _organization_marker(name: str) -> bool:
    # Swift splits Characters (graphemes), so marks/joiners attached to an ASCII
    # marker remain part of its word. An orphan non-alphabetic mark is a separator.
    value, word = name.lower(), ""
    for start, end in _character_spans(value):
        first = value[start]
        if first.isalnum() or WORD_ADDITIONS.fullmatch(first):
            word += value[start:end]
        else:
            if word in ORGANIZATION_MARKERS:
                return True
            word = ""
    return word in ORGANIZATION_MARKERS


def _search_fold(value: str) -> str:
    # CFStringFold strips canonical accents from Roman/Greek/Cyrillic bases;
    # stripping all Mn characters would incorrectly remove Hebrew/Indic vowels.
    folded = []
    index = 0
    while index < len(value):
        original = value[index]
        decomposed = unicodedata.normalize("NFD", original)
        base = decomposed[0] if ord(decomposed[0]) < 0x0510 else original
        mapped = base.casefold()
        if mapped != base and ord(base) < 0x0510:
            mapped = "".join(character for character in mapped if not _extends(character))
        folded.append(mapped)
        index += 1
        attached = []
        while index < len(value) and _extends(value[index]):
            attached.append(value[index])
            index += 1
        if mapped and ord(mapped[-1]) >= 0x0510:
            if base == original and mapped == original and attached and ord(attached[0]) < 0x0510:
                attached.pop(0)
            folded.extend(attached)
    # Swift String substring matching is canonically equivalent.
    return unicodedata.normalize("NFC", "".join(folded))


def _search_text(value: str) -> tuple[str, bytes | None]:
    folded = _search_fold(value)
    boundaries = None
    for start, end in _character_spans(folded):
        if end - start > 1:
            if boundaries is None:
                boundaries = bytearray(b"\1") * (len(folded) + 1)
            boundaries[start + 1:end] = b"\0" * (end - start - 1)
    return folded, bytes(boundaries) if boundaries is not None else None


def _contains_folded(haystack: tuple[str, bytes | None], needle: str) -> bool:
    value, boundaries = haystack
    if boundaries is None:
        return needle in value
    start = value.find(needle)
    while start >= 0:
        if boundaries[start] and boundaries[start + len(needle)]:
            return True
        start = value.find(needle, start + 1)
    return False


def preview_peak_bytes(preview: dict) -> int:
    """Conservative incremental cost, including staging and publication copies.

    Count JSON objects without deduplicating aliases, then reserve three copies
    for staged previews, the immutable workspace and temporary copy/serialization
    work. Per-row overhead covers indices, reason maps, selection/move/Undo sets
    and deepcopy bookkeeping. The name allowance covers Unicode folding's
    three-scalar expansion, four-byte storage and compact boundary buffers.
    """
    pending = [preview]
    retained = 0
    while pending:
        value = pending.pop()
        retained += sys.getsizeof(value)
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return 3 * retained + 2048 + 32 * len(preview["name"])


class ContactWorkspace:
    def __init__(self, previews: list[dict], expected_count: int):
        if (not isinstance(previews, list) or type(expected_count) is not int
                or expected_count < 0 or len(previews) != expected_count):
            raise ValueError("Contact workspace requires a complete verified snapshot")
        identifiers = set()
        for item in previews:
            if (not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] < 0
                    or item["id"] in identifiers or not isinstance(item.get("name"), str)
                    or type(item.get("phoneCount")) is not int or item["phoneCount"] < 0):
                raise ValueError("Contact workspace contains an invalid or duplicate preview")
            identifiers.add(item["id"])
        self._previews = deepcopy(previews)
        self._known_ids = frozenset(identifiers)
        self._original_reasons = {}
        for contact in self._previews:
            if not contact["name"].strip(WHITESPACE):
                reason = "Name is missing"
            elif contact["phoneCount"] == 0:
                reason = "No supported phone number"
            elif _organization_marker(contact["name"]):
                reason = "Organization-like name marker"
            else:
                continue
            self._original_reasons[contact["id"]] = reason
        self._review_ids = set(self._original_reasons)
        self._search_names = [_search_text(contact["name"]) for contact in self._previews]
        self._group, self._query, self._page_start = "Main", "", 0
        self._indices: list[int] = []
        self._rebuild_filter()

    @property
    def previews(self) -> list[dict]:
        return deepcopy(self._previews)

    @property
    def known_ids(self) -> frozenset[int]:
        return self._known_ids

    @property
    def review_ids(self) -> frozenset[int]:
        return frozenset(self._review_ids)

    @property
    def main_ids(self) -> frozenset[int]:
        return self._known_ids.difference(self._review_ids)

    @property
    def main_count(self) -> int:
        return len(self._previews) - len(self._review_ids)

    @property
    def review_count(self) -> int:
        return len(self._review_ids)

    @property
    def filtered_ids(self) -> frozenset[int]:
        return self._filtered_ids

    @property
    def filtered_count(self) -> int:
        return len(self._indices)

    @property
    def visible(self) -> list[dict]:
        return deepcopy([self._previews[index] for index in self._indices[self._page_start:self._page_start + PAGE_SIZE]])

    @property
    def group(self) -> str:
        return self._group

    @property
    def query(self) -> str:
        return self._query

    @property
    def page_start(self) -> int:
        return self._page_start

    @property
    def has_next_page(self) -> bool:
        return self._page_start + PAGE_SIZE < len(self._indices)

    @property
    def has_previous_page(self) -> bool:
        return self._page_start > 0

    @property
    def is_scoped(self) -> bool:
        return self._group != "All" or bool(self._query)

    @property
    def scope_label(self) -> str:
        base = f"{self._group} contacts"
        matching = f"{base} matching “{self._query}”" if self._query else base
        return f"{matching} · {self.filtered_count} across all pages"

    def contains(self, identifier: int) -> bool:
        return type(identifier) is int and identifier in self._known_ids

    def is_in_review(self, identifier: int) -> bool:
        return type(identifier) is int and identifier in self._review_ids

    def reason(self, identifier: int) -> str | None:
        if not self.is_in_review(identifier):
            return None
        return self._original_reasons.get(identifier, "Moved to Review by you")

    def show(self, group: str) -> None:
        if group not in GROUPS:
            raise ValueError("Unknown contact group")
        self._group = group
        self._rebuild_filter()

    def search(self, query: str) -> None:
        if not isinstance(query, str):
            raise TypeError("Contact search must be text")
        self._query = query.strip(WHITESPACE)
        self._rebuild_filter()

    def next_page(self) -> None:
        if self.has_next_page:
            self._page_start += PAGE_SIZE

    def previous_page(self) -> None:
        if self.has_previous_page:
            self._page_start = max(0, self._page_start - PAGE_SIZE)

    def move(self, ids, source: str, destination: str) -> bool:
        if source not in ("Main", "Review") or destination not in ("Main", "Review") or source == destination:
            return False
        try:
            identifiers = set(ids)
        except TypeError:
            return False
        if not identifiers or any(not self.contains(identifier) for identifier in identifiers):
            return False
        if source == "Review" and not identifiers.issubset(self._review_ids):
            return False
        if source == "Main" and not identifiers.isdisjoint(self._review_ids):
            return False
        if destination == "Review":
            self._review_ids.update(identifiers)
        else:
            self._review_ids.difference_update(identifiers)
        self._rebuild_filter()
        return True

    def _rebuild_filter(self) -> None:
        needle = _search_fold(self._query)
        self._indices = [index for index, contact in enumerate(self._previews)
                         if (self._group == "All" or ((contact["id"] in self._review_ids) == (self._group == "Review")))
                         and (not needle or _contains_folded(self._search_names[index], needle))]
        self._filtered_ids = frozenset(self._previews[index]["id"] for index in self._indices)
        self._page_start = 0
