import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from camera_inbox.config import AppConfig, ConfigStore


def test_regular_media_organization_is_enabled_by_default():
    config = AppConfig()
    assert config.organization.organize_regular_media is True
    assert config.live_photo.enabled is True


def test_default_paths_form_a_ready_to_use_sony_inbox_layout():
    config = AppConfig()
    assert config.paths.input == Path("/data/PhotoInbox/sony-camera")
    assert config.paths.output == Path("/data/Photos/01_memories/sony")
    assert config.paths.staging == Path("/data/PhotoInbox/.staging/sony-camera")
    assert config.paths.retention == Path("/data/PhotoInbox/.retention/shotmark-originals")
    assert config.paths.duplicates == Path("/data/PhotoInbox/.duplicates/sony-camera")


def test_example_yaml_uses_the_application_path_defaults():
    example_path = Path(__file__).resolve().parents[1] / "config.example.yaml"
    example = AppConfig.model_validate(YAML(typ="safe").load(example_path.read_text()))
    assert example.paths == AppConfig().paths


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
