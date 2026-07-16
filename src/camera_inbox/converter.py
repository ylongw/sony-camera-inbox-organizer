from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import LivePhotoConfig
from .maker_note import build_apple_maker_note
from .mov_metadata import add_live_photo_metadata, validate_live_photo_movie
from .sony import SonyClipMetadata, SonyShotMark


@dataclass(frozen=True)
class LivePhotoPair:
    image: Path
    video: Path
    mark: SonyShotMark
    content_identifier: str


def _run(command: list[str], timeout: int = 600) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")


def _read_tag(exiftool: Path, path: Path, tag: str) -> str:
    result = subprocess.run(
        [str(exiftool), "-s3", f"-{tag}", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout.strip()


def _read_image_identifier(exiftool: Path, path: Path) -> str:
    # ExifTool 12.19 renamed Apple's tag 0x0011 from ContentIdentifier to
    # MediaGroupUUID. Support both names because NAS distributions vary.
    return _read_tag(exiftool, path, "MediaGroupUUID") or _read_tag(
        exiftool, path, "ContentIdentifier"
    )


def _pair_stem(destination: Path, base: str, index: int) -> str:
    stem = f"{base}_SM{index:02d}"
    if not (destination / f"{stem}.JPG").exists() and not (destination / f"{stem}.MOV").exists():
        return stem
    for collision in range(1, 10_000):
        candidate = f"{stem}_{collision:02d}"
        if not (destination / f"{candidate}.JPG").exists() and not (
            destination / f"{candidate}.MOV"
        ).exists():
            return candidate
    raise RuntimeError(f"unable to allocate Live Photo name for {stem}")


def _write_image_metadata(
    image: Path,
    identifier: str,
    captured_at: datetime,
    exiftool: Path,
    staging: Path,
) -> None:
    maker_note = staging / f".{image.stem}.makernote"
    maker_note.write_bytes(build_apple_maker_note(identifier))
    timestamp = captured_at.strftime("%Y:%m:%d %H:%M:%S")
    _run(
        [
            str(exiftool),
            "-overwrite_original",
            "-all=",
            "-Make=Apple",
            "-Orientation#=1",
            f"-DateTimeOriginal={timestamp}",
            f"-CreateDate={timestamp}",
            f"-ModifyDate={timestamp}",
            f"-MakerNotes<={maker_note}",
            str(image),
        ],
        60,
    )
    maker_note.unlink()


def _publish_staged_pairs(staged: list[tuple[Path, Path, Path, Path]]) -> None:
    for image, video, final_image, final_video in staged:
        os.replace(video, final_video)
        os.replace(image, final_image)


def create_live_photo_pairs(
    source: Path,
    destination: Path,
    captured_at: datetime,
    clip: SonyClipMetadata,
    profile: LivePhotoConfig,
    ffmpeg: Path,
    exiftool: Path,
    staging_root: Path,
) -> tuple[LivePhotoPair, ...]:
    destination.mkdir(mode=0o770, parents=True, exist_ok=True)
    staging_root.mkdir(mode=0o770, parents=True, exist_ok=True)
    if destination.stat().st_dev != staging_root.stat().st_dev:
        raise RuntimeError("staging and output must be on the same filesystem")

    base = captured_at.strftime("%Y-%m-%d_%H-%M-%S")
    output: list[LivePhotoPair] = []
    staged: list[tuple[Path, Path, Path, Path]] = []
    with tempfile.TemporaryDirectory(prefix="shotmark-", dir=staging_root) as temporary:
        staging = Path(temporary)
        for index, mark in enumerate(clip.marks, 1):
            stem = _pair_stem(destination, base, index)
            image = staging / f"{stem}.JPG"
            video = staging / f"{stem}.MOV"
            encoded_video = staging / f"{stem}.encoded.MOV"
            final_image = destination / image.name
            final_video = destination / video.name
            identifier = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "sony-camera-inbox-livephoto:v1:"
                    f"{source.name}:{source.stat().st_size}:{captured_at.isoformat()}:{mark.frame}",
                )
            ).upper()

            start = max(
                0.0,
                min(
                    mark.elapsed_seconds - profile.duration_seconds / 2,
                    clip.duration_seconds - profile.duration_seconds,
                ),
            )
            length = min(profile.duration_seconds, clip.duration_seconds - start)
            if length <= 0:
                raise RuntimeError(f"Shot Mark is outside the video duration: {mark.label}")
            _run(
                [
                    "nice",
                    "-n",
                    "10",
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{mark.elapsed_seconds:.6f}",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(image),
                ]
            )
            _write_image_metadata(image, identifier, captured_at, exiftool, staging)

            creation_date = clip.creation_date or captured_at.isoformat()
            _run(
                [
                    "nice",
                    "-n",
                    "10",
                    str(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{start:.6f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{length:.6f}",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    f"scale=-2:{profile.height}:flags=lanczos,fps={profile.fps},format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-threads",
                    str(profile.video_threads),
                    "-preset",
                    profile.preset,
                    "-crf",
                    str(profile.crf),
                    "-profile:v",
                    "high",
                    "-level:v",
                    "4.2",
                    "-tag:v",
                    "avc1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    profile.audio_bitrate,
                    "-movflags",
                    "+faststart+use_metadata_tags",
                    "-movie_timescale",
                    "600",
                    "-video_track_timescale",
                    "600",
                    "-map_metadata",
                    "-1",
                    "-brand",
                    "qt  ",
                    "-metadata",
                    f"com.apple.quicktime.content.identifier={identifier}",
                    "-metadata",
                    "com.apple.quicktime.live-photo.auto=1",
                    "-metadata",
                    f"creation_time={creation_date}",
                    "-metadata:s:v:0",
                    "handler_name=Core Media Video",
                    "-metadata:s:a:0",
                    "handler_name=Core Media Audio",
                    str(encoded_video),
                ]
            )
            add_live_photo_metadata(encoded_video, video, identifier, length / 2)
            encoded_video.unlink()
            if _read_image_identifier(exiftool, image) != identifier:
                raise RuntimeError(f"image content identifier validation failed: {image}")
            if not _read_tag(exiftool, image, "LivePhotoVideoIndex"):
                raise RuntimeError(f"image LivePhotoVideoIndex validation failed: {image}")
            for forbidden in ("GPSPosition", "ThumbnailImage", "ImageCaptureRequestID", "PhotoIdentifier"):
                if _read_tag(exiftool, image, forbidden):
                    raise RuntimeError(f"image contains forbidden private metadata {forbidden}: {image}")
            if _read_tag(exiftool, video, "ContentIdentifier") != identifier:
                raise RuntimeError(f"video content identifier validation failed: {video}")
            validate_live_photo_movie(video, identifier, length / 2)
            _run(
                [
                    str(ffmpeg),
                    "-v",
                    "error",
                    "-xerror",
                    "-i",
                    str(video),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-f",
                    "null",
                    "-",
                ],
                120,
            )
            os.chmod(image, 0o660)
            os.chmod(video, 0o660)
            staged.append((image, video, final_image, final_video))
            output.append(LivePhotoPair(final_image, final_video, mark, identifier))
        _publish_staged_pairs(staged)
    return tuple(output)


def retain_original(source: Path, retention_root: Path, captured_at: datetime) -> Path:
    directory = retention_root / captured_at.strftime("%Y/%m/%d")
    directory.mkdir(mode=0o770, parents=True, exist_ok=True)
    target = directory / f"{captured_at:%Y-%m-%d_%H-%M-%S}--{source.name}"
    for index in range(1, 10_000):
        if not target.exists():
            break
        target = directory / f"{captured_at:%Y-%m-%d_%H-%M-%S}--{index:02d}--{source.name}"
    else:
        raise RuntimeError(f"unable to allocate retention path for {source}")
    os.replace(source, target)
    os.utime(target, None)
    return target


def cleanup_retention(retention_root: Path, retention_days: int, now: float | None = None) -> int:
    if not retention_root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = 0
    for path in retention_root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    for directory in sorted(
        (path for path in retention_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed
