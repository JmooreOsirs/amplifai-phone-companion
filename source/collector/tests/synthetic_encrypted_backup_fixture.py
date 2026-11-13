"""Fictional encrypted iOS backup fixtures. Never uses a device/account."""

from __future__ import annotations

import hashlib
import io
import json
import plistlib
import sqlite3
import struct
import tempfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from amplifai_phone.agent import run_connect
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN, collect_iphone, parse_selected_backup
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from large_backup_probe import archived_entry

SYNTHETIC_PASSWORD = "Synthetic only! 42"
WRAPPING = bytes(range(32))
FILE_KEY = bytes(reversed(range(32)))


def flip(value):
    return value[:-1] + bytes([value[-1] ^ 1])


def tlv(tag, value):
    data = struct.pack(">I", value) if isinstance(value, int) else value
    return tag + struct.pack(">I", len(data)) + data


def keybag(password, variant):
    salt, dpsl = b"s" * 20, b"d" * 20
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), dpsl, 2, 32)
    derived = hashlib.pbkdf2_hmac("sha1", derived, salt, 2, 32)
    wrapped = aes_key_wrap(derived, WRAPPING)
    if variant == "keybag_corrupt":
        wrapped = flip(wrapped)
    root = [
        (b"UUID", b"r" * 16),
        (b"TYPE", 1),
        (b"WRAP", 2),
        (b"SALT", salt),
        (b"ITER", 2),
        (b"DPSL", dpsl),
        (b"DPIC", 2),
    ]
    group = [
        (b"UUID", b"c" * 16),
        (b"CLAS", 1),
        (b"WRAP", 2),
        (b"KTYP", 0),
        (b"WPKY", wrapped),
    ]
    if variant == "keybag_extra_tlv":
        group.append((b"JUNK", b"j" * 8))
    if variant == "keybag_missing_tlv":
        group.pop(3)
    result = b"".join(tlv(*item) for item in root + group)
    if variant == "keybag_second_class_corrupt":
        result += b"".join(tlv(*item) for item in [
            (b"UUID", b"z" * 16), (b"CLAS", 2), (b"WRAP", 2),
            (b"KTYP", 0), (b"WPKY", flip(wrapped)),
        ])
    if variant == "keybag_truncated":
        result = result[:-7]
    return result


def encrypt(data, padded=True):
    if padded:
        p = padding.PKCS7(128).padder()
        data = p.update(data) + p.finalize()
    e = Cipher(algorithms.AES(FILE_KEY), modes.CBC(b"\0" * 16)).encryptor()
    return e.update(data) + e.finalize()


def metadata(relative, size, wrapped):
    archive = plistlib.loads(archived_entry(relative, size))
    archive["$objects"][1]["EncryptionKey"] = plistlib.UID(4)
    archive["$objects"].extend(
        [
            {"$class": plistlib.UID(5), "NS.data": wrapped},
            {
                "$classname": "NSMutableData",
                "$classes": ["NSMutableData", "NSData", "NSObject"],
            },
        ]
    )
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def fixture(root, password, variant):
    root.mkdir()
    manifest_plain = root.parent / "manifest-plain.sqlite"
    wrapped = struct.pack("<I", 1) + aes_key_wrap(WRAPPING, FILE_KEY)
    schemas = {
        "contacts": "CREATE TABLE ABPerson (First TEXT, Last TEXT); CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT); INSERT INTO ABPerson VALUES ('Fictional', 'Only'); INSERT INTO ABMultiValue VALUES (1,3,'+12025550101');",
        "calls": "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER); INSERT INTO ZCALLRECORD VALUES (800000000,65,'+12025550101',1,1);",
        "messages": "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT); CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date, service, is_from_me, handle_id, text); INSERT INTO handle VALUES (1,'+12025550101'); INSERT INTO message VALUES (1,800000000,'SMS',0,1,NULL);",
    }
    with closing(sqlite3.connect(manifest_plain)) as db, db:
        db.execute(
            "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
        )
        for category, schema in schemas.items():
            plain = root.parent / f"{category}.sqlite"
            with closing(sqlite3.connect(plain)) as con, con:
                con.executescript(schema)
            data = plain.read_bytes()
            relative = DATABASES[category]
            file_id = hashlib.sha1((HOME_DOMAIN + "-" + relative).encode()).hexdigest()
            location = root / file_id[:2] / file_id
            location.parent.mkdir(exist_ok=True)
            payload = encrypt(data)
            if variant == f"{category}_padding":
                payload = flip(payload)
            location.write_bytes(payload)
            entry_key = wrapped
            if variant == f"{category}_key" or (
                variant == "both_optional_keys" and category in {"calls", "messages"}
            ):
                entry_key = flip(entry_key)
            if variant == f"{category}_missing_class":
                entry_key = struct.pack("<I", 99) + wrapped[4:]
            if variant == f"{category}_short_key":
                entry_key = wrapped[:7]
            db.execute(
                "INSERT INTO Files VALUES (?,?,?,1,?)",
                (
                    file_id,
                    HOME_DOMAIN,
                    relative,
                    metadata(relative, len(data), entry_key),
                ),
            )
            for suffix in ["wal", "shm"]:
                if variant == f"{category}_{suffix}_key":
                    side_relative = relative + "-" + suffix
                    side_id = hashlib.sha1(
                        (HOME_DOMAIN + "-" + side_relative).encode()
                    ).hexdigest()
                    side_location = root / side_id[:2] / side_id
                    side_location.parent.mkdir(exist_ok=True)
                    side_plaintext = b"Fictional sidecar; must never be parsed"
                    side_location.write_bytes(encrypt(side_plaintext))
                    db.execute(
                        "INSERT INTO Files VALUES (?,?,?,1,?)",
                        (
                            side_id,
                            HOME_DOMAIN,
                            side_relative,
                            metadata(side_relative, len(side_plaintext), flip(wrapped)),
                        ),
                    )
    manifest_cipher = encrypt(manifest_plain.read_bytes(), False)
    if variant == "manifest_truncated_cipher":
        manifest_cipher = manifest_cipher[:-1]
    (root / "Manifest.db").write_bytes(manifest_cipher)
    manifest_key = flip(wrapped) if variant == "manifest_key" else wrapped
    if variant == "manifest_short_key":
        manifest_key = wrapped[:7]
    if variant == "manifest_missing_class":
        manifest_key = struct.pack("<I", 99) + wrapped[4:]
    (root / "Manifest.plist").write_bytes(
        plistlib.dumps(
            {
                "IsEncrypted": True,
                "BackupKeyBag": keybag(password, variant),
                "ManifestKey": manifest_key,
                "Lockdown": {"ProductVersion": "18.0"},
            }
        )
    )
    for name in ("Info.plist", "Status.plist"):
        (root / name).write_bytes(plistlib.dumps({}))


@contextmanager
def acquisition(root, variant="valid", password=SYNTHETIC_PASSWORD):
    """Mock one USB acquisition while retaining production unlock and cleanup."""
    device = SimpleNamespace(is_usb=True, is_network=False,
                             connection_type="USB", serial="SYNTHETIC-UNLOCK")
    lockdown = MagicMock()
    lockdown.udid = device.serial
    lockdown.__aenter__ = AsyncMock(return_value=lockdown)
    lockdown.__aexit__ = AsyncMock(return_value=False)
    service = MagicMock()
    service.__aenter__ = AsyncMock(return_value=service)
    service.__aexit__ = AsyncMock(return_value=False)
    service.service._ensure_started = AsyncMock(return_value=(MagicMock(), MagicMock()))
    service.resolve_backup_selection.return_value = ("synthetic-selection",)

    async def backup(**options):
        fixture(Path(options["backup_directory"]) / device.serial, password, variant)

    service.backup = AsyncMock(side_effect=backup)

    async def collector(**options):
        return await collect_iphone(**options, sessions_root=root,
                                    now=datetime(2026, 11, 13, tzinfo=UTC))

    with (
        patch("pymobiledevice3.usbmux.list_devices", new=AsyncMock(return_value=[device])),
        patch("pymobiledevice3.lockdown.create_using_usbmux", new=AsyncMock(return_value=lockdown)),
        patch("pymobiledevice3.services.mobilebackup2.Mobilebackup2Service", return_value=service),
    ):
        yield collector, service


def probe(variant="valid", password=SYNTHETIC_PASSWORD, entered=None):
    with tempfile.TemporaryDirectory(prefix="amplifai-fictional-repro-") as temporary:
        root = Path(temporary) / "backup"
        fixture(root, password, variant)

        async def collector(**kwargs):
            supplied = kwargs["password_provider"]()
            return parse_selected_backup(
                root,
                supplied,
                now=datetime(2026, 11, 6, tzinfo=UTC),
                transfer_callback=kwargs["transfer_callback"],
                payload_diagnostic_callback=kwargs["payload_diagnostic_callback"],
            )

        supplied = password if entered is None else entered
        src = io.StringIO(
            json.dumps({"action": "password", "value": supplied}, ensure_ascii=False)
            + "\n"
            + json.dumps({"action": "disconnect"})
            + "\n"
        )
        out = io.StringIO()
        status = run_connect(src, out, collector)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        assert supplied not in out.getvalue(), (
            "Synthetic password leaked in event stream"
        )
        failures = [x for x in events if x.get("kind") == "error"]
        captures = [x for x in events if x.get("kind") == "capture"]
        return {
            "variant": variant,
            "exit": status,
            "error": failures[-1] if failures else None,
            "capture": {
                k: captures[-1][k]
                for k in (
                    "totalContacts",
                    "availableCalls",
                    "availableMessages",
                    "missing",
                    "unavailableReasons",
                )
                if k in captures[-1]
            }
            if captures
            else None,
            "diagnostics": [
                x for x in events if x.get("kind") == "selected-payload-diagnostic"
            ],
        }
