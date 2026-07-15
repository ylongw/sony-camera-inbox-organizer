import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.media import media_capture_datetime, regular_destination


def test_regular_media_uses_exif_capture_time():
    captured = media_capture_datetime(
        {"DateTimeOriginal": "2026:07:15 22:10:09+08:00"}, Path("photo.JPG")
    )
    assert captured == datetime(2026, 7, 15, 22, 10, 9)
    assert regular_destination(Path("/photos/2026/07/15"), captured, ".JPG") == Path(
        "/photos/2026/07/15/2026-07-15_22-10-09.JPG"
    )


def test_regular_media_without_capture_time_is_rejected():
    with pytest.raises(ValueError, match="no usable capture date"):
        media_capture_datetime({}, Path("unknown.JPG"))
