import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.sony import parse_sony_clip


def test_parses_embedded_sony_shot_marks_without_reading_mdat():
    xml = b'''<NonRealTimeMeta xmlns="urn:test">
      <Duration value="720"/><CreationDate value="2026-07-13T22:57:11+08:00"/>
      <VideoFrame captureFps="59.94p" formatFps="59.94p"/>
      <Device manufacturer="Sony" modelName="ILCE-7CM2"/>
      <Lens modelName="FE 20-70mm F4 G"/>
      <KlvPacket key="060E2B34010101050301020A02000000" frameCount="80"
        lengthValue="0A5F53686F744D61726B31" status="spot"/>
      <KlvPacket key="060E2B34010101050301020A02000000" frameCount="430"
        lengthValue="0A5F53686F744D61726B32" status="spot"/>
    </NonRealTimeMeta>'''
    metadata_box = (len(xml) + 8).to_bytes(4, "big") + b"uuid" + xml
    media_box = (16).to_bytes(4, "big") + b"mdat" + b"12345678"
    with tempfile.TemporaryDirectory() as directory:
        clip_path = Path(directory) / "clip.MP4"
        clip_path.write_bytes(metadata_box + media_box)
        clip = parse_sony_clip(clip_path)

    assert clip is not None
    assert [mark.frame for mark in clip.marks] == [80, 430]
    assert [mark.label for mark in clip.marks] == ["_ShotMark1", "_ShotMark2"]
    assert abs(clip.duration_seconds - 12.012) < 0.001
