import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.app import create_app
from camera_inbox.config import AppConfig, ConfigStore


def test_web_pages_and_manual_scan_endpoint(tmp_path):
    data = tmp_path / "data"
    (data / "inbox").mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    store = ConfigStore(config_path)
    config = AppConfig.model_validate(
        {
            "paths": {
                "input": data / "inbox",
                "output": data / "photos",
                "staging": data / "staging",
                "retention": data / "retention",
                "duplicates": data / "duplicates",
            },
            "automation": {"enabled": False},
        }
    )
    store.save(config)

    with TestClient(create_app(config_path, tmp_path / "state.sqlite", start_worker=False)) as client:
        for path in ("/", "/settings", "/yaml", "/api/status", "/health"):
            assert client.get(path).status_code == 200
        assert "https://github.com/ylongw/sony-camera-inbox-organizer" in client.get("/").text
        assert client.post("/api/scan").status_code == 202
