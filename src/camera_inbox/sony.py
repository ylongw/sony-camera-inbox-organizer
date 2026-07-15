from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


ESSENCE_MARK_KEY = "060E2B34010101050301020A02000000"
FPS = {
    "23.98": Fraction(24000, 1001),
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60, 1),
    "100": Fraction(100, 1),
    "119.88": Fraction(120000, 1001),
    "120": Fraction(120, 1),
}


@dataclass(frozen=True)
class SonyShotMark:
    label: str
    frame: int
    elapsed_seconds: float


@dataclass(frozen=True)
class SonyClipMetadata:
    capture_fps: Fraction
    duration_frames: int
    creation_date: str
    make: str
    model: str
    lens: str
    marks: tuple[SonyShotMark, ...]

    @property
    def duration_seconds(self) -> float:
        return self.duration_frames / float(self.capture_fps)


def _extract_nrt(buffer: bytes) -> str | None:
    start = buffer.find(b"<NonRealTimeMeta")
    if start < 0:
        return None
    end_tag = b"</NonRealTimeMeta>"
    end = buffer.find(end_tag, start)
    if end < 0:
        return None
    return buffer[start : end + len(end_tag)].decode("utf-8", "replace")


def _top_level_boxes(stream):
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    offset = 0
    while offset + 8 <= file_size:
        stream.seek(offset)
        header = stream.read(8)
        size = int.from_bytes(header[:4], "big")
        kind = header[4:8]
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                break
            size = int.from_bytes(extended, "big")
        elif size == 0:
            size = file_size - offset
        if size < 8 or offset + size > file_size:
            break
        yield kind, offset, size
        offset += size


def read_non_real_time_metadata(path: Path) -> str | None:
    """Read Sony XML metadata without loading a potentially huge mdat box."""
    with path.open("rb") as stream:
        for kind, offset, size in _top_level_boxes(stream):
            if kind == b"mdat":
                continue
            stream.seek(offset)
            document = _extract_nrt(stream.read(min(size, 16 * 1024 * 1024)))
            if document is not None:
                return document
    if path.stat().st_size <= 64 * 1024 * 1024:
        return _extract_nrt(path.read_bytes())
    return None


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _first(root: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in root.iter() if _local_name(item) == name), None)


def _fps(value: str) -> Fraction:
    normalized = re.sub(r"[pPiI]$", "", value.strip())
    if normalized in FPS:
        return FPS[normalized]
    return Fraction(normalized)


def parse_sony_clip(path: Path) -> SonyClipMetadata | None:
    document = read_non_real_time_metadata(path)
    if document is None:
        return None
    root = ET.fromstring(document)
    video = _first(root, "VideoFrame")
    duration = _first(root, "Duration")
    if video is None or duration is None:
        raise ValueError("Sony metadata is missing VideoFrame or Duration")
    capture_fps = _fps(video.get("captureFps") or video.get("formatFps") or "30")

    marks: list[SonyShotMark] = []
    for packet in (item for item in root.iter() if _local_name(item) == "KlvPacket"):
        if (packet.get("key") or "").upper() != ESSENCE_MARK_KEY:
            continue
        raw = bytes.fromhex(packet.get("lengthValue") or "")
        if not raw:
            continue
        label = raw[1 : 1 + raw[0]].decode("ascii", "replace")
        if not label.startswith("_ShotMark"):
            continue
        frame = int(packet.get("frameCount") or 0)
        marks.append(SonyShotMark(label, frame, frame / float(capture_fps)))

    device = _first(root, "Device")
    lens = _first(root, "Lens")
    creation = _first(root, "CreationDate")
    return SonyClipMetadata(
        capture_fps=capture_fps,
        duration_frames=int(duration.get("value") or 0),
        creation_date=creation.get("value", "") if creation is not None else "",
        make=device.get("manufacturer", "SONY") if device is not None else "SONY",
        model=device.get("modelName", "") if device is not None else "",
        lens=lens.get("modelName", "") if lens is not None else "",
        marks=tuple(marks),
    )
