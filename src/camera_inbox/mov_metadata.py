#!/usr/bin/env python3
"""Inject an Apple-compatible still-image-time metadata track into a MOV."""

from __future__ import annotations

import os
import stat
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


QUICKTIME_EPOCH_OFFSET = 2_082_844_800
METADATA_TIMESCALE = 600
STILL_IMAGE_KEY = b"com.apple.quicktime.still-image-time"
STILL_IMAGE_SAMPLE = b"\x00\x00\x00\x09\x00\x00\x00\x01\x00"
CONTENT_IDENTIFIER_KEY = b"com.apple.quicktime.content.identifier"
AUTO_LIVE_PHOTO_KEY = b"com.apple.quicktime.live-photo.auto"
CONTAINER_TYPES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"dinf",
    b"edts",
    b"udta",
}


class MovMetadataError(ValueError):
    pass


@dataclass(frozen=True)
class Box:
    offset: int
    size: int
    kind: bytes
    header_size: int = 8

    @property
    def payload_offset(self) -> int:
        return self.offset + self.header_size

    @property
    def end(self) -> int:
        return self.offset + self.size


def _boxes(data: bytes | bytearray, start: int, end: int) -> tuple[Box, ...]:
    output: list[Box] = []
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise MovMetadataError("truncated MOV box header")
        size, kind = struct.unpack_from(">I4s", data, offset)
        header_size = 8
        if size == 1:
            if offset + 16 > end:
                raise MovMetadataError("truncated extended MOV box header")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header_size = 16
        elif size == 0:
            size = end - offset
        if size < header_size or offset + size > end:
            raise MovMetadataError(f"invalid {kind!r} box size: {size}")
        output.append(Box(offset, size, kind, header_size))
        offset += size
    return tuple(output)


def _box(kind: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size > 0xFFFFFFFF:
        raise MovMetadataError(f"{kind!r} box is too large")
    return struct.pack(">I4s", size, kind) + payload


def _full_box(kind: bytes, payload: bytes = b"", *, version: int = 0, flags: int = 0) -> bytes:
    return _box(kind, bytes((version,)) + flags.to_bytes(3, "big") + payload)


def _direct_child(data: bytes | bytearray, parent: Box, kind: bytes) -> Box:
    for child in _boxes(data, parent.payload_offset, parent.end):
        if child.kind == kind:
            return child
    raise MovMetadataError(f"missing {kind.decode('latin1')} box")


def _movie_timescale(data: bytes | bytearray, moov: Box) -> int:
    mvhd = _direct_child(data, moov, b"mvhd")
    version = data[mvhd.payload_offset]
    timescale_offset = mvhd.offset + (20 if version == 0 else 28)
    timescale = struct.unpack_from(">I", data, timescale_offset)[0]
    if timescale <= 0:
        raise MovMetadataError("invalid movie timescale")
    return timescale


def _track_id(data: bytes | bytearray, track: Box) -> int:
    tkhd = _direct_child(data, track, b"tkhd")
    version = data[tkhd.payload_offset]
    track_id_offset = tkhd.offset + (20 if version == 0 else 28)
    return struct.unpack_from(">I", data, track_id_offset)[0]


def _metadata_track(
    track_id: int,
    movie_timescale: int,
    still_time_seconds: float,
    chunk_offset: int,
) -> bytes:
    if chunk_offset > 0xFFFFFFFF:
        raise MovMetadataError("Live Photo metadata offset exceeds 32-bit stco range")
    timestamp = int(time.time()) + QUICKTIME_EPOCH_OFFSET
    empty_duration = max(0, round(still_time_seconds * movie_timescale))
    marker_duration = max(1, round(movie_timescale / METADATA_TIMESCALE))
    track_duration = empty_duration + marker_duration
    matrix = struct.pack(
        ">9i",
        0x00010000,
        0,
        0,
        0,
        0x00010000,
        0,
        0,
        0,
        0x40000000,
    )
    tkhd = _full_box(
        b"tkhd",
        struct.pack(">IIIII", timestamp, timestamp, track_id, 0, track_duration)
        + b"\0" * 8
        + struct.pack(">hhhh", 0, 0, 0, 0)
        + matrix
        + struct.pack(">II", 0, 0),
        flags=0x0F,
    )
    elst = _full_box(
        b"elst",
        struct.pack(">I", 2)
        + struct.pack(">IiHH", empty_duration, -1, 1, 0)
        + struct.pack(">IiHH", marker_duration, 0, 1, 0),
    )
    edts = _box(b"edts", elst)
    mdhd = _full_box(
        b"mdhd",
        struct.pack(
            ">IIIIHH",
            timestamp,
            timestamp,
            METADATA_TIMESCALE,
            1,
            0x55C4,
            0,
        ),
    )
    media_name = b"Core Media Metadata"
    media_handler = _full_box(
        b"hdlr",
        b"mhlrmetaappl"
        + struct.pack(">II", 1, 0)
        + bytes((len(media_name),))
        + media_name,
    )
    gmin = _full_box(
        b"gmin",
        struct.pack(">HHHHhH", 0x40, 0x8000, 0x8000, 0x8000, 0, 0),
    )
    gmhd = _box(b"gmhd", gmin)
    data_name = b"Core Media Data Handler"
    data_handler = _full_box(
        b"hdlr",
        b"dhlralisappl"
        + struct.pack(">II", 0, 0)
        + bytes((len(data_name),))
        + data_name,
    )
    alias = _full_box(b"alis", flags=1)
    dref = _full_box(b"dref", struct.pack(">I", 1) + alias)
    dinf = _box(b"dinf", dref)

    key_description = (
        struct.pack(">I4s4s", 12 + len(STILL_IMAGE_KEY), b"keyd", b"mdta")
        + STILL_IMAGE_KEY
    )
    data_type = _box(b"dtyp", struct.pack(">II", 0, 0x41))
    keys = _box(
        b"keys",
        struct.pack(">II", 8 + len(key_description) + len(data_type), 1)
        + key_description
        + data_type,
    )
    mebx = _box(b"mebx", b"\0" * 6 + struct.pack(">H", 1) + keys)
    stsd = _full_box(b"stsd", struct.pack(">I", 1) + mebx)
    stts = _full_box(b"stts", struct.pack(">III", 1, 1, 1))
    stsc = _full_box(b"stsc", struct.pack(">IIII", 1, 1, 1, 1))
    stsz = _full_box(b"stsz", struct.pack(">II", len(STILL_IMAGE_SAMPLE), 1))
    stco = _full_box(b"stco", struct.pack(">II", 1, chunk_offset))
    stbl = _box(b"stbl", stsd + stts + stsc + stsz + stco)
    minf = _box(b"minf", gmhd + data_handler + dinf + stbl)
    mdia = _box(b"mdia", mdhd + media_handler + minf)
    return _box(b"trak", tkhd + edts + mdia)


def _movie_metadata(content_identifier: str) -> bytes:
    try:
        identifier = content_identifier.encode("ascii")
    except UnicodeEncodeError as error:
        raise MovMetadataError("content identifier must be ASCII") from error
    if not identifier or b"\0" in identifier:
        raise MovMetadataError("content identifier must not be empty or contain NUL")

    # AVFoundation writes QuickTime-style meta directly under moov. In
    # particular, live-photo.auto is signed int8 rather than the UTF-8 string
    # emitted by FFmpeg's use_metadata_tags mode.
    handler = _box(b"hdlr", b"\0" * 8 + b"mdta" + b"\0" * 14)
    keys = _full_box(
        b"keys",
        struct.pack(">I", 2)
        + _box(b"mdta", CONTENT_IDENTIFIER_KEY)
        + _box(b"mdta", AUTO_LIVE_PHOTO_KEY),
    )
    identifier_data = _box(b"data", struct.pack(">II", 1, 0) + identifier)
    auto_data = _box(b"data", struct.pack(">II", 0x15, 0) + b"\x01")
    items = _box(
        b"ilst",
        _box(struct.pack(">I", 1), identifier_data)
        + _box(struct.pack(">I", 2), auto_data),
    )
    return _box(b"meta", handler + keys + items)


def _patch_chunk_offsets(
    data: bytearray,
    start: int,
    end: int,
    threshold: int,
    delta: int,
) -> None:
    for box in _boxes(data, start, end):
        if box.kind in {b"stco", b"co64"}:
            count = struct.unpack_from(">I", data, box.offset + 12)[0]
            width = 4 if box.kind == b"stco" else 8
            value_format = ">I" if width == 4 else ">Q"
            for index in range(count):
                offset = box.offset + 16 + index * width
                value = struct.unpack_from(value_format, data, offset)[0]
                if value >= threshold:
                    value += delta
                    if width == 4 and value > 0xFFFFFFFF:
                        raise MovMetadataError("existing stco offset exceeds 32-bit range")
                    struct.pack_into(value_format, data, offset, value)
        elif box.kind in CONTAINER_TYPES:
            _patch_chunk_offsets(
                data,
                box.payload_offset,
                box.end,
                threshold,
                delta,
            )
        elif box.kind == b"meta":
            _patch_chunk_offsets(
                data,
                box.payload_offset + 4,
                box.end,
                threshold,
                delta,
            )


def _set_next_track_id(data: bytearray, moov: Box, next_track_id: int) -> None:
    mvhd = _direct_child(data, moov, b"mvhd")
    struct.pack_into(">I", data, mvhd.end - 4, next_track_id)


def add_live_photo_metadata(
    source: Path,
    destination: Path,
    content_identifier: str,
    still_time_seconds: float = 1.5,
) -> None:
    if source.resolve() == destination.resolve():
        raise MovMetadataError("source and destination must differ")
    if destination.exists():
        raise MovMetadataError(f"destination already exists: {destination}")
    if not 0 <= still_time_seconds <= 60:
        raise MovMetadataError("still-image-time is outside the supported range")

    original = source.read_bytes()
    top_level = _boxes(original, 0, len(original))
    ftyp_matches = [box for box in top_level if box.kind == b"ftyp"]
    if len(ftyp_matches) != 1:
        raise MovMetadataError("MOV must contain exactly one ftyp box")
    ftyp = ftyp_matches[0]
    if ftyp.size < 16 or original[ftyp.payload_offset : ftyp.payload_offset + 4] != b"qt  ":
        raise MovMetadataError("MOV must use the QuickTime qt brand")

    moov_matches = [box for box in top_level if box.kind == b"moov"]
    if len(moov_matches) != 1:
        raise MovMetadataError("MOV must contain exactly one moov box")
    moov = moov_matches[0]
    if moov.header_size != 8:
        raise MovMetadataError("extended-size moov boxes are not supported")

    mdat_matches = [box for box in top_level if box.kind == b"mdat"]
    if len(mdat_matches) != 1:
        raise MovMetadataError("MOV must contain exactly one mdat box")
    mdat = mdat_matches[0]
    if mdat.header_size != 8:
        raise MovMetadataError("extended-size mdat boxes are not supported")
    if moov.end > mdat.offset:
        raise MovMetadataError("MOV must place moov before mdat")
    if mdat.end != len(original):
        raise MovMetadataError("mdat must be the final MOV box")

    children = _boxes(original, moov.payload_offset, moov.end)
    if any(box.kind == b"meta" for box in children):
        raise MovMetadataError("MOV already contains direct movie metadata")
    udta_matches = [box for box in children if box.kind == b"udta"]
    if len(udta_matches) > 1:
        raise MovMetadataError("MOV contains multiple udta boxes")
    old_metadata = udta_matches[0] if udta_matches else None
    if old_metadata is not None:
        udta_children = _boxes(original, old_metadata.payload_offset, old_metadata.end)
        if len(udta_children) != 1 or udta_children[0].kind != b"meta":
            raise MovMetadataError("udta contains data other than FFmpeg movie metadata")

    movie_timescale = _movie_timescale(original, moov)
    track_ids = [_track_id(original, box) for box in children if box.kind == b"trak"]
    track_id = max(track_ids, default=0) + 1
    placeholder = _metadata_track(track_id, movie_timescale, still_time_seconds, 0)
    movie_metadata = _movie_metadata(content_identifier)
    removed_size = old_metadata.size if old_metadata is not None else 0
    delta = len(placeholder) + len(movie_metadata) - removed_size
    chunk_offset = mdat.end + delta
    metadata_track = _metadata_track(
        track_id,
        movie_timescale,
        still_time_seconds,
        chunk_offset,
    )

    patched_moov = bytearray(original[moov.offset : moov.end])
    local_moov = Box(0, len(patched_moov), b"moov")
    _patch_chunk_offsets(patched_moov, 8, len(patched_moov), moov.end, delta)
    _set_next_track_id(patched_moov, local_moov, track_id + 1)
    local_children = _boxes(patched_moov, 8, len(patched_moov))
    local_old_metadata = next((box for box in local_children if box.kind == b"udta"), None)
    insertion = local_old_metadata.offset if local_old_metadata is not None else len(patched_moov)
    suffix = local_old_metadata.end if local_old_metadata is not None else insertion
    new_moov = _box(
        b"moov",
        bytes(patched_moov[8:insertion])
        + metadata_track
        + movie_metadata
        + bytes(patched_moov[suffix:]),
    )

    # fnOS mobile only plays the AVFoundation-style single-mdat layout. Keep
    # the marker sample inside the primary media-data box instead of creating
    # a second, otherwise valid, mdat at EOF.
    patched_original = bytearray(original)
    struct.pack_into(">I", patched_original, ftyp.payload_offset + 4, 0)
    encoded_mdat_size = struct.unpack_from(">I", patched_original, mdat.offset)[0]
    if encoded_mdat_size != 0:
        expanded_mdat_size = encoded_mdat_size + len(STILL_IMAGE_SAMPLE)
        if expanded_mdat_size > 0xFFFFFFFF:
            raise MovMetadataError("expanded mdat exceeds 32-bit box size")
        struct.pack_into(">I", patched_original, mdat.offset, expanded_mdat_size)
    result = (
        bytes(patched_original[: moov.offset])
        + new_moov
        + bytes(patched_original[moov.end : mdat.end])
        + STILL_IMAGE_SAMPLE
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, stat.S_IMODE(source.stat().st_mode))
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def read_live_photo_still_time(path: Path) -> float | None:
    data = path.read_bytes()
    moov = next((box for box in _boxes(data, 0, len(data)) if box.kind == b"moov"), None)
    if moov is None:
        return None
    movie_timescale = _movie_timescale(data, moov)
    for track in _boxes(data, moov.payload_offset, moov.end):
        if track.kind != b"trak":
            continue
        payload = data[track.offset : track.end]
        if b"mebx" not in payload or STILL_IMAGE_KEY not in payload:
            continue
        edts = _direct_child(data, track, b"edts")
        elst = _direct_child(data, edts, b"elst")
        if struct.unpack_from(">I", data, elst.offset + 12)[0] < 2:
            return None
        empty_duration = struct.unpack_from(">I", data, elst.offset + 16)[0]
        media_time = struct.unpack_from(">i", data, elst.offset + 20)[0]
        if media_time != -1:
            return None

        chunk_box = next(
            (
                box
                for box in _walk_boxes(data, track.payload_offset, track.end)
                if box.kind in {b"stco", b"co64"}
            ),
            None,
        )
        if chunk_box is None or struct.unpack_from(">I", data, chunk_box.offset + 12)[0] != 1:
            return None
        value_format = ">I" if chunk_box.kind == b"stco" else ">Q"
        chunk_offset = struct.unpack_from(value_format, data, chunk_box.offset + 16)[0]
        if data[chunk_offset : chunk_offset + len(STILL_IMAGE_SAMPLE)] != STILL_IMAGE_SAMPLE:
            return None
        return empty_duration / movie_timescale
    return None


def validate_live_photo_movie(
    path: Path,
    content_identifier: str,
    still_time_seconds: float,
) -> None:
    data = path.read_bytes()
    top_level = _boxes(data, 0, len(data))

    ftyp_matches = [box for box in top_level if box.kind == b"ftyp"]
    if len(ftyp_matches) != 1:
        raise MovMetadataError("Live Photo MOV must contain exactly one ftyp box")
    ftyp = ftyp_matches[0]
    if data[ftyp.payload_offset : ftyp.payload_offset + 4] != b"qt  ":
        raise MovMetadataError("Live Photo MOV does not use the QuickTime qt brand")
    if struct.unpack_from(">I", data, ftyp.payload_offset + 4)[0] != 0:
        raise MovMetadataError("Live Photo MOV minor version is not zero")

    moov_matches = [box for box in top_level if box.kind == b"moov"]
    if len(moov_matches) != 1:
        raise MovMetadataError("Live Photo MOV must contain exactly one moov box")
    moov = moov_matches[0]
    if _movie_timescale(data, moov) != METADATA_TIMESCALE:
        raise MovMetadataError("Live Photo MOV movie timescale is not 600")

    children = _boxes(data, moov.payload_offset, moov.end)
    if any(box.kind == b"udta" for box in children):
        raise MovMetadataError("Live Photo MOV contains legacy udta metadata")
    meta_matches = [box for box in children if box.kind == b"meta"]
    if len(meta_matches) != 1:
        raise MovMetadataError("Live Photo MOV must contain one direct moov/meta box")
    meta = meta_matches[0]
    if data[meta.offset : meta.end] != _movie_metadata(content_identifier):
        raise MovMetadataError("Live Photo MOV movie metadata differs from AVFoundation")

    video_timescales: list[int] = []
    metadata_tracks = 0
    for track in (box for box in children if box.kind == b"trak"):
        mdia = _direct_child(data, track, b"mdia")
        handler = _direct_child(data, mdia, b"hdlr")
        if handler.size < 20:
            raise MovMetadataError("Live Photo MOV contains a truncated media handler")
        handler_type = data[handler.payload_offset + 8 : handler.payload_offset + 12]
        if handler_type == b"vide":
            mdhd = _direct_child(data, mdia, b"mdhd")
            version = data[mdhd.payload_offset]
            timescale_offset = mdhd.offset + (20 if version == 0 else 28)
            video_timescales.append(struct.unpack_from(">I", data, timescale_offset)[0])
        if b"mebx" in data[track.offset : track.end] and STILL_IMAGE_KEY in data[track.offset : track.end]:
            metadata_tracks += 1
    if video_timescales != [METADATA_TIMESCALE]:
        raise MovMetadataError("Live Photo MOV video timescale is not 600")
    if metadata_tracks != 1:
        raise MovMetadataError("Live Photo MOV must contain one still-image-time track")

    mdat_matches = [box for box in top_level if box.kind == b"mdat"]
    if len(mdat_matches) != 1 or mdat_matches[0].end != len(data):
        raise MovMetadataError("Live Photo MOV must end with one primary mdat box")
    if data[-len(STILL_IMAGE_SAMPLE) :] != STILL_IMAGE_SAMPLE:
        raise MovMetadataError("Live Photo marker sample is not inside the primary mdat")

    actual_still_time = read_live_photo_still_time(path)
    if actual_still_time is None or abs(actual_still_time - still_time_seconds) > 0.01:
        raise MovMetadataError("Live Photo still-image-time does not match the clip midpoint")


def _walk_boxes(data: bytes, start: int, end: int):
    for box in _boxes(data, start, end):
        yield box
        if box.kind in CONTAINER_TYPES:
            yield from _walk_boxes(data, box.payload_offset, box.end)
        elif box.kind == b"meta":
            yield from _walk_boxes(data, box.payload_offset + 4, box.end)
