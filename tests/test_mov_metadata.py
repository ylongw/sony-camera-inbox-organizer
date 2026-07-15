import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.mov_metadata import (
    STILL_IMAGE_SAMPLE,
    _box,
    _boxes,
    _direct_child,
    _full_box,
    _walk_boxes,
    add_live_photo_metadata,
    read_live_photo_still_time,
    validate_live_photo_movie,
)


def synthetic_mov(chunk_offset: int) -> bytes:
    ftyp = _box(b"ftyp", b"qt  " + struct.pack(">I", 0) + b"qt  ")
    mvhd = _full_box(
        b"mvhd",
        struct.pack(">IIII", 0, 0, 600, 600) + b"\0" * 76 + struct.pack(">I", 2),
    )
    tkhd = _full_box(
        b"tkhd",
        struct.pack(">IIIII", 0, 0, 1, 0, 600) + b"\0" * 60,
        flags=0x0F,
    )
    mdhd = _full_box(
        b"mdhd",
        struct.pack(">IIIIHH", 0, 0, 600, 600, 0x55C4, 0),
    )
    handler = _full_box(b"hdlr", b"\0" * 4 + b"vide" + b"\0" * 12 + b"VideoHandler\0")
    stco = _full_box(b"stco", struct.pack(">II", 1, chunk_offset))
    media = _box(b"mdia", mdhd + handler + _box(b"minf", _box(b"stbl", stco)))
    track = _box(b"trak", tkhd + media)
    ffmpeg_metadata = _box(b"udta", _box(b"meta", b"\0" * 4))
    moov = _box(b"moov", mvhd + track + ffmpeg_metadata)
    return ftyp + moov + _box(b"mdat", b"VIDEO")


class LivePhotoMovMetadataTest(unittest.TestCase):
    def test_injects_timed_mebx_and_preserves_existing_chunk_offsets(self):
        placeholder = synthetic_mov(0)
        top_level = _boxes(placeholder, 0, len(placeholder))
        original_mdat = next(box for box in top_level if box.kind == b"mdat")
        source_bytes = synthetic_mov(original_mdat.payload_offset)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.MOV"
            output = root / "output.MOV"
            source.write_bytes(source_bytes)
            identifier = "E0B00F73-B6B1-4B53-9F30-D4FF164CE89A"
            add_live_photo_metadata(source, output, identifier, 1.5)

            self.assertAlmostEqual(read_live_photo_still_time(output), 1.5)
            validate_live_photo_movie(output, identifier, 1.5)
            result = output.read_bytes()
            moov = next(box for box in _boxes(result, 0, len(result)) if box.kind == b"moov")
            first_track = next(
                box
                for box in _boxes(result, moov.payload_offset, moov.end)
                if box.kind == b"trak"
            )
            stco = next(
                box
                for box in _walk_boxes(result, first_track.payload_offset, first_track.end)
                if box.kind == b"stco"
            )
            media_offset = struct.unpack_from(">I", result, stco.offset + 16)[0]
            self.assertEqual(result[media_offset : media_offset + 5], b"VIDEO")

            tracks = [
                box
                for box in _boxes(result, moov.payload_offset, moov.end)
                if box.kind == b"trak"
            ]
            metadata_stco = next(
                box
                for box in _walk_boxes(result, tracks[-1].payload_offset, tracks[-1].end)
                if box.kind == b"stco"
            )
            marker_offset = struct.unpack_from(">I", result, metadata_stco.offset + 16)[0]
            mdats = [box for box in _boxes(result, 0, len(result)) if box.kind == b"mdat"]
            self.assertEqual(len(mdats), 1)
            self.assertEqual(mdats[0].end, len(result))
            self.assertEqual(marker_offset, len(result) - len(STILL_IMAGE_SAMPLE))
            self.assertEqual(
                result[marker_offset : marker_offset + len(STILL_IMAGE_SAMPLE)],
                STILL_IMAGE_SAMPLE,
            )

            top_level = _boxes(result, 0, len(result))
            ftyp = next(box for box in top_level if box.kind == b"ftyp")
            self.assertEqual(struct.unpack_from(">I", result, ftyp.payload_offset + 4)[0], 0)
            moov_children = _boxes(result, moov.payload_offset, moov.end)
            self.assertNotIn(b"udta", {box.kind for box in moov_children})
            movie_metadata = next(box for box in moov_children if box.kind == b"meta")
            self.assertEqual(movie_metadata.size, 240)
            metadata_payload = result[movie_metadata.offset : movie_metadata.end]
            self.assertIn(identifier.encode("ascii"), metadata_payload)
            self.assertIn(
                b"\x00\x00\x00\x11data\x00\x00\x00\x15\x00\x00\x00\x00\x01",
                metadata_payload,
            )

            mvhd = _direct_child(result, moov, b"mvhd")
            self.assertEqual(struct.unpack_from(">I", result, mvhd.end - 4)[0], 3)

            corrupted = root / "corrupted.MOV"
            corrupted_bytes = bytearray(result)
            auto_value = corrupted_bytes.rfind(
                b"\x00\x00\x00\x11data\x00\x00\x00\x15\x00\x00\x00\x00\x01"
            )
            self.assertGreaterEqual(auto_value, 0)
            corrupted_bytes[auto_value + 12] = 1
            corrupted.write_bytes(corrupted_bytes)
            with self.assertRaisesRegex(Exception, "differs from AVFoundation"):
                validate_live_photo_movie(corrupted, identifier, 1.5)


if __name__ == "__main__":
    unittest.main()
