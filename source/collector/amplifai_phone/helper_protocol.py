"""UTF-8 contract for the private helper's line-delimited JSON pipes.

The Windows parent decoder does not set the child's standard-stream encoding.
Configure the actual streams as well as the launch environment so frozen and
source helpers preserve Unicode independently of the Windows ANSI code page.
"""

from __future__ import annotations

from typing import TextIO


def configure_protocol_streams(source: TextIO | None, sink: TextIO) -> None:
    """Pin real text pipes; leave injected in-memory test streams unchanged.

    Strict encoding avoids silently replacing a contact or password character.
    Only standard TextIO-style reconfiguration is used; no new stream owns or
    closes the caller's buffer. Input accepts the parent's platform newlines,
    while output always emits one LF-delimited UTF-8 JSON frame.
    """
    if source is not None and callable(getattr(source, "reconfigure", None)):
        source.reconfigure(encoding="utf-8", errors="strict", newline=None)
    if callable(getattr(sink, "reconfigure", None)):
        sink.reconfigure(encoding="utf-8", errors="strict", newline="\n", write_through=True)
