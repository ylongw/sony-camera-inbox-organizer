from __future__ import annotations

import fcntl
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(StrictModel):
    input: Path = Path("/data/PhotoInbox/sony-camera")
    output: Path = Path("/data/Photos/01_memories/sony")
    staging: Path = Path("/data/PhotoInbox/.staging/sony-camera")
    retention: Path = Path("/data/PhotoInbox/.retention/shotmark-originals")
    duplicates: Path = Path("/data/PhotoInbox/.duplicates/sony-camera")


class AutomationConfig(StrictModel):
    enabled: bool = True
    recursive: bool = False
    poll_seconds: float = Field(default=2.0, ge=0.5, le=3600)
    stable_cycles: int = Field(default=3, ge=1, le=100)
    minimum_age_seconds: float = Field(default=4.0, ge=0, le=86400)


class OrganizationConfig(StrictModel):
    organize_regular_media: bool = True
    sort_by_capture_date: bool = True
    date_pattern: str = "%Y/%m/%d"


class LivePhotoConfig(StrictModel):
    enabled: bool = True
    duration_seconds: float = Field(default=3.0, ge=1.0, le=10.0)
    height: int = Field(default=1080, ge=480, le=2160)
    fps: int = Field(default=30, ge=15, le=60)
    crf: int = Field(default=18, ge=0, le=40)
    preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"
    ] = "veryfast"
    video_threads: int = Field(default=2, ge=1, le=32)
    audio_bitrate: str = "192k"


class OriginalsConfig(StrictModel):
    action: Literal["archive", "keep"] = "archive"
    retention_days: int = Field(default=30, ge=1, le=3650)


class HooksConfig(StrictModel):
    after_publish: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1, le=3600)


class AppConfig(StrictModel):
    schema_version: Literal[1] = 1
    paths: PathsConfig = Field(default_factory=PathsConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    organization: OrganizationConfig = Field(default_factory=OrganizationConfig)
    live_photo: LivePhotoConfig = Field(default_factory=LivePhotoConfig)
    originals: OriginalsConfig = Field(default_factory=OriginalsConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)

    @model_validator(mode="after")
    def validate_paths(self) -> "AppConfig":
        paths = [
            self.paths.input,
            self.paths.output,
            self.paths.staging,
            self.paths.retention,
            self.paths.duplicates,
        ]
        normalized = [path.expanduser().resolve(strict=False) for path in paths]
        if len(set(normalized)) != len(normalized):
            raise ValueError("input, output, staging, and retention paths must differ")
        input_path, output_path, staging_path, retention_path, duplicates_path = normalized
        for managed in (output_path, staging_path, retention_path, duplicates_path):
            if managed.is_relative_to(input_path):
                raise ValueError("managed output directories cannot be inside input")
        if not self.organization.date_pattern or ".." in self.organization.date_pattern:
            raise ValueError("organization.date_pattern is invalid")
        return self


def _merge_document(target, source: dict) -> None:
    for key in list(target):
        if key not in source:
            del target[key]
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_document(target[key], value)
        else:
            target[key] = value


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self._thread_lock = threading.RLock()

    def ensure(self) -> AppConfig:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save(AppConfig())
        return self.load()

    def load(self) -> AppConfig:
        with self._thread_lock:
            yaml = YAML(typ="safe")
            with self._file_lock(shared=True):
                document = yaml.load(self.path.read_text(encoding="utf-8")) or {}
            return AppConfig.model_validate(document)

    def read_raw(self) -> str:
        with self._thread_lock, self._file_lock(shared=True):
            return self.path.read_text(encoding="utf-8")

    def save_raw(self, value: str) -> AppConfig:
        yaml = YAML(typ="safe")
        config = AppConfig.model_validate(yaml.load(value) or {})
        self._atomic_write(value if value.endswith("\n") else value + "\n")
        return config

    def save(self, config: AppConfig) -> None:
        config = AppConfig.model_validate(config)
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        with self._thread_lock:
            if self.path.exists():
                with self._file_lock(shared=True):
                    document = yaml.load(self.path.read_text(encoding="utf-8")) or {}
            else:
                document = {}
            _merge_document(document, config.model_dump(mode="json"))
            output = io.StringIO()
            yaml.dump(document, output)
            self._atomic_write(output.getvalue())

    def _atomic_write(self, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self._file_lock(shared=False):
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()

    def _file_lock(self, *, shared: bool):
        class Lock:
            def __init__(inner_self, path: Path):
                inner_self.path = path
                inner_self.stream = None

            def __enter__(inner_self):
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                inner_self.stream = inner_self.path.open("a+")
                operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(inner_self.stream.fileno(), operation)
                return inner_self

            def __exit__(inner_self, *_args):
                assert inner_self.stream is not None
                fcntl.flock(inner_self.stream.fileno(), fcntl.LOCK_UN)
                inner_self.stream.close()

        return Lock(self.lock_path)
