from __future__ import annotations

import struct


LIVE_PHOTO_VIDEO_INDEX = 0x0000000200502024


def build_apple_maker_note(content_identifier: str) -> bytes:
    """Build the minimal Apple MakerNote needed by Live Photo consumers."""
    try:
        identifier = content_identifier.encode("ascii") + b"\0"
    except UnicodeEncodeError as error:
        raise ValueError("content identifier must be ASCII") from error
    if len(identifier) != 37 or identifier.count(b"-") != 4:
        raise ValueError("content identifier must be a canonical UUID")

    header = b"Apple iOS\0\0\1MM"
    directory_size = 2 + 3 * 12 + 4
    identifier_offset = len(header) + directory_size
    # Apple MakerNotes align the following 64-bit value to a two-byte boundary.
    video_index_offset = (identifier_offset + len(identifier) + 1) & ~1
    padding = b"\0" * (video_index_offset - identifier_offset - len(identifier))
    entries = (
        struct.pack(">H", 3)
        + struct.pack(">HHI4s", 0x0001, 9, 1, struct.pack(">i", 16))
        + struct.pack(">HHII", 0x0011, 2, len(identifier), identifier_offset)
        + struct.pack(">HHII", 0x0017, 16, 1, video_index_offset)
        + struct.pack(">I", 0)
    )
    return header + entries + identifier + padding + struct.pack(">Q", LIVE_PHOTO_VIDEO_INDEX)
