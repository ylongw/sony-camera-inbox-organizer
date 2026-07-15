from __future__ import annotations

import logging
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import AppConfig, ConfigStore
from .converter import cleanup_retention, create_live_photo_pairs, retain_original
from .media import (
    SUPPORTED_EXTENSIONS,
    duplicate_destination,
    files_are_identical,
    media_capture_datetime,
    read_media_metadata,
    regular_destination,
    unique_path,
)
from .sony import parse_sony_clip
from .state import StateStore, utc_now


LOG = logging.getLogger(__name__)
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def capture_datetime(value: str, source: Path) -> datetime:
    if value:
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.replace(tzinfo=None)
        except ValueError:
            LOG.warning("invalid Sony CreationDate %r in %s", value, source)
    return datetime.fromtimestamp(source.stat().st_mtime)


def fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve(strict=False)}:{stat.st_size}:{stat.st_mtime_ns}"


def runtime_path_errors(config: AppConfig) -> list[str]:
    errors: list[str] = []
    input_path = config.paths.input
    if not input_path.is_dir():
        errors.append(f"input directory does not exist: {input_path}")
    for name, path in (
        ("output", config.paths.output),
        ("staging", config.paths.staging),
        ("retention", config.paths.retention),
        ("duplicates", config.paths.duplicates),
    ):
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not os.access(path, os.W_OK | os.X_OK):
                errors.append(f"{name} directory is not writable: {path}")
        except OSError as error:
            errors.append(f"cannot create {name} directory {path}: {error}")
    existing = [config.paths.output, config.paths.staging, config.paths.duplicates]
    if config.originals.action == "archive":
        existing.append(config.paths.retention)
    if all(path.exists() for path in existing):
        devices = {path.stat().st_dev for path in existing}
        if len(devices) != 1:
            errors.append("output, staging, and retention must use the same filesystem")
    return errors


class Worker:
    def __init__(
        self,
        config_store: ConfigStore,
        state_store: StateStore,
        *,
        ffmpeg: Path = Path("/usr/bin/ffmpeg"),
        exiftool: Path = Path("/usr/bin/exiftool"),
    ):
        self.config_store = config_store
        self.state_store = state_store
        self.ffmpeg = ffmpeg
        self.exiftool = exiftool
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._manual_lock = threading.Lock()
        self._manual_requested = False
        self._thread: threading.Thread | None = None
        self._observed: dict[Path, tuple[int, int, int]] = {}
        self._last_cleanup = 0.0
        self._status_lock = threading.Lock()
        self._status = {
            "state": "starting",
            "current_file": None,
            "last_scan_at": None,
            "last_error": None,
            "last_scan_processed": 0,
            "last_scan_candidates": 0,
            "manual_pending": False,
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="shotmark-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def request_scan(self) -> None:
        with self._manual_lock:
            self._manual_requested = True
        self._set_status(manual_pending=True)
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def _set_status(self, **changes) -> None:
        with self._status_lock:
            self._status.update(changes)

    def _take_manual(self) -> bool:
        with self._manual_lock:
            requested = self._manual_requested
            self._manual_requested = False
        self._set_status(manual_pending=False)
        return requested

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                config = self.config_store.load()
                manual = self._take_manual()
                if config.automation.enabled or manual:
                    self._scan(config, manual=manual)
                else:
                    self._set_status(state="paused", current_file=None, last_error=None)
                wait_seconds = config.automation.poll_seconds
            except Exception as error:
                LOG.exception("worker loop failed")
                self._set_status(state="error", current_file=None, last_error=str(error))
                wait_seconds = 5.0
            self._wake.wait(wait_seconds)
            self._wake.clear()
        self._set_status(state="stopped", current_file=None)

    def _candidates(self, config: AppConfig) -> list[Path]:
        root = config.paths.input
        iterator = root.rglob("*") if config.automation.recursive else root.glob("*")
        return sorted(
            path
            for path in iterator
            if path.is_file()
            and not path.is_symlink()
            and not path.name.startswith(".")
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    def _is_stable(self, path: Path, config: AppConfig, *, manual: bool) -> bool:
        stat = path.stat()
        if time.time() - stat.st_mtime < config.automation.minimum_age_seconds:
            return False
        previous = self._observed.get(path)
        signature = (stat.st_size, stat.st_mtime_ns)
        cycles = previous[2] + 1 if previous and previous[:2] == signature else 1
        self._observed[path] = (signature[0], signature[1], cycles)
        return manual or cycles >= config.automation.stable_cycles

    def _scan(self, config: AppConfig, *, manual: bool) -> None:
        errors = runtime_path_errors(config)
        if errors:
            raise RuntimeError("; ".join(errors))
        self._set_status(state="scanning", current_file=None, last_error=None)
        candidates = self._candidates(config)
        processed = 0
        for source in candidates:
            if self._stop.is_set() or not self._is_stable(source, config, manual=manual):
                continue
            item_fingerprint = fingerprint(source)
            previous = self.state_store.find_fingerprint(item_fingerprint)
            if previous and previous["status"] in {"succeeded", "duplicate", "running"}:
                continue
            if previous and previous["status"] == "deferred" and not config.organization.organize_regular_media:
                continue
            if previous and previous["status"] == "failed" and not manual:
                continue
            self._process(source, item_fingerprint, config)
            processed += 1
        self._observed = {path: value for path, value in self._observed.items() if path.exists()}
        now = time.time()
        if config.originals.action == "archive" and now - self._last_cleanup >= 86400:
            removed = cleanup_retention(config.paths.retention, config.originals.retention_days, now)
            if removed:
                LOG.info("removed %d expired retained originals", removed)
            self._last_cleanup = now
        self._set_status(
            state="idle" if config.automation.enabled else "paused",
            current_file=None,
            last_scan_at=utc_now(),
            last_scan_processed=processed,
            last_scan_candidates=len(candidates),
        )

    def _process(self, source: Path, item_fingerprint: str, config: AppConfig) -> None:
        started = time.monotonic()
        job_id = self.state_store.start(item_fingerprint, source)
        self._set_status(state="processing", current_file=str(source), last_error=None)
        try:
            clip = parse_sony_clip(source) if source.suffix.lower() in VIDEO_EXTENSIONS else None
            if clip is not None and clip.marks and config.live_photo.enabled:
                self._process_live_photo(job_id, source, clip, config, started)
                return
            if not config.organization.organize_regular_media:
                self.state_store.finish(
                    job_id,
                    "deferred",
                    message="Regular media organization is disabled",
                    elapsed_seconds=time.monotonic() - started,
                )
                return
            self._process_regular(job_id, source, config, started)
        except Exception as error:
            LOG.exception("failed to process %s", source)
            self.state_store.finish(
                job_id,
                "failed",
                error=str(error),
                elapsed_seconds=time.monotonic() - started,
            )
            self._set_status(last_error=f"{source.name}: {error}")

    @staticmethod
    def _dated_directory(config: AppConfig, captured_at: datetime) -> Path:
        destination = config.paths.output
        if config.organization.sort_by_capture_date:
            relative = Path(captured_at.strftime(config.organization.date_pattern))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("organization.date_pattern produced an unsafe path")
            destination = destination / relative
        return destination

    def _process_live_photo(self, job_id, source, clip, config, started) -> None:
        captured_at = capture_datetime(clip.creation_date, source)
        destination = self._dated_directory(config, captured_at)
        pairs = create_live_photo_pairs(
            source,
            destination,
            captured_at,
            clip,
            config.live_photo,
            self.ffmpeg,
            self.exiftool,
            config.paths.staging,
        )
        retained_path = None
        if config.originals.action == "archive":
            retained_path = str(retain_original(source, config.paths.retention, captured_at))
        outputs = [str(path) for pair in pairs for path in (pair.image, pair.video)]
        hook_error = self._run_hook(config, "live_photo", source, destination, outputs)
        self.state_store.finish(
            job_id,
            "succeeded",
            marks=len(pairs),
            outputs=outputs,
            retained_path=retained_path,
            message=hook_error,
            elapsed_seconds=time.monotonic() - started,
        )

    def _process_regular(self, job_id, source, config, started) -> None:
        metadata = read_media_metadata(self.exiftool, source)
        captured_at = media_capture_datetime(metadata, source)
        destination = self._dated_directory(config, captured_at)
        destination.mkdir(mode=0o770, parents=True, exist_ok=True)
        target = regular_destination(destination, captured_at, source.suffix)
        if target.exists() and files_are_identical(source, target):
            duplicate = duplicate_destination(config.paths.duplicates, source, captured_at)
            duplicate.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
            os.replace(source, duplicate)
            self.state_store.finish(
                job_id,
                "duplicate",
                outputs=[str(target)],
                retained_path=str(duplicate),
                message="Duplicate moved outside the output library",
                elapsed_seconds=time.monotonic() - started,
            )
            return
        target = unique_path(target)
        os.replace(source, target)
        hook_error = self._run_hook(config, "regular", source, destination, [str(target)])
        self.state_store.finish(
            job_id,
            "succeeded",
            outputs=[str(target)],
            message=hook_error,
            elapsed_seconds=time.monotonic() - started,
        )

    def _run_hook(
        self,
        config: AppConfig,
        job_kind: str,
        source: Path,
        destination: Path,
        outputs: list[str],
    ) -> str | None:
        if not config.hooks.after_publish:
            return None
        environment = os.environ.copy()
        environment.update(
            {
                "CAMERA_INBOX_SOURCE": str(source),
                "CAMERA_INBOX_JOB_KIND": job_kind,
                "CAMERA_INBOX_OUTPUT_DIRECTORY": str(destination),
                "CAMERA_INBOX_OUTPUTS_JSON": json.dumps(outputs),
            }
        )
        try:
            result = subprocess.run(
                config.hooks.after_publish,
                capture_output=True,
                text=True,
                timeout=config.hooks.timeout_seconds,
                check=False,
                env=environment,
            )
        except Exception as error:
            LOG.exception("after-publish hook failed to start")
            return f"Published successfully; hook failed: {error}"
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            return f"Published successfully; hook failed: {detail}"
        return None
