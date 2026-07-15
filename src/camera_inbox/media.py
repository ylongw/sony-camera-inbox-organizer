from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path


SUPPORTED_EXTENSIONS = {
    ".arw",
    ".avi",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".m4v",
    ".mov",
    ".mp4",
    ".mts",
    ".png",
}
DATE_TAGS = ("DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate")
DATE_RE = re.compile(
    r"^(?P<year>\d{4}):(?P<month>\d{2}):(?P<day>\d{2})[ T]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


def read_media_metadata(exiftool: Path, path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            str(exiftool),
            "-j",
            "-api",
            "QuickTimeUTC=1",
            "-FileType",
            "-DateTimeOriginal",
            "-CreateDate",
            "-MediaCreateDate",
            "-TrackCreateDate",
            "-Error",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError("ExifTool returned an unexpected response")
    metadata = payload[0]
    if metadata.get("Error"):
        raise ValueError(str(metadata["Error"]))
    if not metadata.get("FileType"):
        raise ValueError("file type could not be detected")
    return metadata


def parse_exif_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = DATE_RE.match(value)
    if not match:
        return None
    try:
        return datetime(**{key: int(item) for key, item in match.groupdict().items()})
    except ValueError:
        return None


def media_capture_datetime(metadata: dict[str, object], source: Path) -> datetime:
    for tag in DATE_TAGS:
        captured_at = parse_exif_datetime(metadata.get(tag))
        if captured_at is not None:
            return captured_at
    raise ValueError(f"no usable capture date in media metadata: {source.name}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_are_identical(first: Path, second: Path) -> bool:
    return first.stat().st_size == second.stat().st_size and sha256(first) == sha256(second)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}_{index:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"unable to allocate a unique name for {path}")


def regular_destination(root: Path, captured_at: datetime, suffix: str) -> Path:
    return root / f"{captured_at:%Y-%m-%d_%H-%M-%S}{suffix}"


def duplicate_destination(root: Path, source: Path, captured_at: datetime) -> Path:
    directory = root / captured_at.strftime("%Y/%m/%d")
    candidate = directory / f"{captured_at:%Y-%m-%d_%H-%M-%S}--duplicate--{source.name}"
    return unique_path(candidate)
