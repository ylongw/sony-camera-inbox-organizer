import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox import converter


def test_image_orientation_is_written_as_a_raw_exif_value(tmp_path, monkeypatch):
    image = tmp_path / "cover.JPG"
    image.write_bytes(b"jpeg")
    commands = []

    monkeypatch.setattr(converter, "_run", lambda command, timeout=600: commands.append(command))
    converter._write_image_metadata(
        image,
        "E0B00F73-B6B1-4B53-9F30-D4FF164CE89A",
        datetime(2026, 7, 16, 0, 0, 0),
        Path("/usr/bin/exiftool"),
        tmp_path,
    )

    assert "-Orientation#=1" in commands[0]
    assert "-Orientation=1" not in commands[0]
