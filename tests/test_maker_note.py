import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.maker_note import LIVE_PHOTO_VIDEO_INDEX, build_apple_maker_note


def test_builds_minimal_deterministic_apple_maker_note():
    identifier = "E0B00F73-B6B1-4B53-9F30-D4FF164CE89A"
    value = build_apple_maker_note(identifier)

    assert value.startswith(b"Apple iOS\0\0\1MM")
    assert len(value) == 102
    assert struct.unpack_from(">H", value, 14)[0] == 3
    assert identifier.encode() + b"\0" in value
    assert value.endswith(struct.pack(">Q", LIVE_PHOTO_VIDEO_INDEX))
    assert build_apple_maker_note(identifier) == value


def test_rejects_non_uuid_content_identifier():
    with pytest.raises(ValueError, match="canonical UUID"):
        build_apple_maker_note("not-a-uuid")
