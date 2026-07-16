from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _section(filename: str, start: str, end: str) -> str:
    text = (ROOT / filename).read_text(encoding="utf-8")
    return text.split(start, 1)[1].split(end, 1)[0]


def test_quick_starts_are_image_first_and_use_the_public_web_port():
    for filename, start, end in (
        ("README.md", "## Quick Start", "## Processing Rules"),
        ("README.zh-CN.md", "## 快速开始", "## 分支逻辑"),
    ):
        quick_start = _section(filename, start, end)
        assert "git clone" not in quick_start
        assert "docker compose pull" in quick_start
        assert "docker compose up -d" in quick_start
        assert "docker.io/ylongwang/sony-camera-inbox-organizer:latest" in quick_start
        assert '"18088:8080"' in quick_start
        assert "http://NAS-IP:18088" in quick_start
        assert "http://NAS-IP:8080" not in quick_start


def test_source_checkout_is_kept_in_the_development_sections():
    assert "git clone" in _section("README.md", "## Development", "## License")
    assert "git clone" in _section("README.zh-CN.md", "## 开发", "## 许可证")
