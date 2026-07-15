import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.config import AppConfig, ConfigStore


def test_regular_media_organization_is_enabled_by_default():
    config = AppConfig()
    assert config.organization.organize_regular_media is True
    assert config.live_photo.enabled is True


def test_yaml_is_single_source_of_truth(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.ensure()
    raw = store.read_raw().replace("organize_regular_media: true", "organize_regular_media: false")
    store.save_raw(raw)
    assert store.load().organization.organize_regular_media is False


def test_rejects_output_inside_input():
    with pytest.raises(ValueError, match="cannot be inside input"):
        AppConfig.model_validate(
            {
                "paths": {
                    "input": "/data/inbox",
                    "output": "/data/inbox/output",
                    "staging": "/data/staging",
                    "retention": "/data/retention",
                    "duplicates": "/data/duplicates",
                }
            }
        )
