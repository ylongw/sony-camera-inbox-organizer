from pathlib import Path

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]


def test_container_publish_is_manual_and_selects_a_source_revision():
    yaml = YAML(typ="safe")
    workflow = yaml.load(ROOT.joinpath(".github/workflows/publish.yml"))

    triggers = workflow["on"]
    assert set(triggers) == {"workflow_dispatch"}
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["source_ref"]["default"] == "main"
    assert inputs["publish_latest"]["type"] == "boolean"
    assert "release_tag" in inputs


def test_container_publish_pushes_and_verifies_both_platforms():
    text = ROOT.joinpath(".github/workflows/publish.yml").read_text(encoding="utf-8")

    assert "docker.io/ylongwang/sony-camera-inbox-organizer" in text
    assert "ghcr.io" not in text
    assert "packages: write" not in text
    assert "DOCKERHUB_USERNAME" in text
    assert "DOCKERHUB_TOKEN" in text
    assert "platforms: linux/amd64,linux/arm64" in text
    assert "push: true" in text
    assert "Verify published platforms" in text
    assert "fetch-depth: 0" in text
    assert 'git checkout --detach "$revision"' in text
    assert "type=raw,value=sha-${{ steps.source.outputs.short_sha }}" in text
