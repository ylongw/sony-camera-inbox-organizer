from __future__ import annotations

import logging
import os
import shlex
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import __version__
from .config import AppConfig, ConfigStore
from .state import StateStore
from .worker import Worker, runtime_path_errors


LOG = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parent


def _checked(form, name: str) -> bool:
    return form.get(name) in {"on", "true", "1", "yes"}


def _form_config(form) -> AppConfig:
    hook = shlex.split(str(form.get("hook_command", "")))
    return AppConfig.model_validate(
        {
            "schema_version": 1,
            "paths": {
                "input": form.get("input_path"),
                "output": form.get("output_path"),
                "staging": form.get("staging_path"),
                "retention": form.get("retention_path"),
                "duplicates": form.get("duplicates_path"),
            },
            "automation": {
                "enabled": _checked(form, "automatic_enabled"),
                "recursive": _checked(form, "recursive"),
                "poll_seconds": form.get("poll_seconds"),
                "stable_cycles": form.get("stable_cycles"),
                "minimum_age_seconds": form.get("minimum_age_seconds"),
            },
            "organization": {
                "organize_regular_media": _checked(form, "organize_regular_media"),
                "sort_by_capture_date": _checked(form, "sort_by_capture_date"),
                "date_pattern": form.get("date_pattern"),
            },
            "live_photo": {
                "enabled": _checked(form, "live_photo_enabled"),
                "duration_seconds": form.get("duration_seconds"),
                "height": form.get("height"),
                "fps": form.get("fps"),
                "crf": form.get("crf"),
                "preset": form.get("preset"),
                "video_threads": form.get("video_threads"),
                "audio_bitrate": form.get("audio_bitrate"),
            },
            "originals": {
                "action": form.get("original_action"),
                "retention_days": form.get("retention_days"),
            },
            "hooks": {
                "after_publish": hook,
                "timeout_seconds": form.get("hook_timeout_seconds"),
            },
        }
    )


def create_app(
    config_path: Path | None = None,
    state_path: Path | None = None,
    *,
    start_worker: bool = True,
) -> FastAPI:
    config_path = config_path or Path(os.environ.get("CONFIG_PATH", "/config/config.yaml"))
    state_path = state_path or Path(os.environ.get("STATE_PATH", "/config/state.sqlite"))
    config_store = ConfigStore(config_path)
    config_store.ensure()
    state_store = StateStore(state_path)
    worker = Worker(config_store, state_store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="Sony Camera Inbox Organizer", version=__version__, lifespan=lifespan)
    app.state.config_store = config_store
    app.state.state_store = state_store
    app.state.worker = worker
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")

    def template_context(request: Request, **values):
        return {
            "request": request,
            "version": __version__,
            "config": config_store.load(),
            "worker": worker.status(),
            **values,
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=template_context(request, jobs=state_store.latest(50)),
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request):
        config = config_store.load()
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=template_context(
                request,
                form_config=config,
                path_errors=runtime_path_errors(config),
                error=None,
            ),
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def save_settings(request: Request):
        form = await request.form()
        try:
            config = _form_config(form)
            config_store.save(config)
            worker.wake()
        except (ValidationError, ValueError) as error:
            current = config_store.load()
            return templates.TemplateResponse(
                request=request,
                name="settings.html",
                status_code=422,
                context=template_context(
                    request,
                    form_config=current,
                    path_errors=runtime_path_errors(current),
                    error=str(error),
                ),
            )
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/yaml", response_class=HTMLResponse)
    async def yaml_editor(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="yaml.html",
            context=template_context(request, yaml_text=config_store.read_raw(), error=None),
        )

    @app.post("/yaml", response_class=HTMLResponse)
    async def save_yaml(request: Request):
        form = await request.form()
        yaml_text = str(form.get("yaml_text", ""))
        try:
            config_store.save_raw(yaml_text)
            worker.wake()
        except Exception as error:
            return templates.TemplateResponse(
                request=request,
                name="yaml.html",
                status_code=422,
                context=template_context(request, yaml_text=yaml_text, error=str(error)),
            )
        return RedirectResponse("/yaml?saved=1", status_code=303)

    @app.get("/api/status")
    async def api_status():
        config = config_store.load()
        return {
            **worker.status(),
            "automatic_enabled": config.automation.enabled,
            "config_path": str(config_path),
            "path_errors": runtime_path_errors(config),
        }

    @app.get("/api/jobs")
    async def api_jobs():
        return {"jobs": state_store.latest(50)}

    @app.post("/api/scan", status_code=202)
    async def api_scan():
        worker.request_scan()
        return {"accepted": True}

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    async def api_retry(job_id: int):
        job = state_store.get(job_id)
        if job is None:
            return JSONResponse({"error": "job not found"}, status_code=404)
        if job["status"] != "failed":
            return JSONResponse({"error": "only failed jobs can be retried"}, status_code=409)
        worker.request_scan()
        return {"accepted": True}

    @app.get("/health")
    async def health():
        status = worker.status()
        healthy = status["state"] != "error"
        return JSONResponse(
            {"status": "ok" if healthy else "error", "worker": status},
            status_code=200 if healthy else 503,
        )

    return app


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
