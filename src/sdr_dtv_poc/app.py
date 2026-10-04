# SPDX-License-Identifier: GPL-3.0-or-later
import secrets
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .artifacts import NAME, chunks, open_registered, validate_playlist
from .config import Settings
from .manager import Conflict, Manager, Unavailable
from .models import (
    Artifact,
    Diagnostic,
    Diagnostics,
    EndReason,
    Error,
    InputKind,
    Playback,
    Recording,
    RecordingStart,
    Scan,
    ScanStart,
    Service,
    Session,
    SessionStart,
    State,
)
from .security import Protection

STATIC = Path(__file__).with_name("static")
# WebUI files owned by #18/#27-#29; only these names are served from static/.
UI_FILES = {
    "style.css": "text/css",
    "api.js": "text/javascript",
    "player.js": "text/javascript",
    "mock.js": "text/javascript",
    "app.js": "text/javascript",
}
ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": Error} for code in (403, 404, 409, 422, 501, 503)
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.manager = Manager(settings)
        try:
            yield
        finally:
            await app.state.manager.close()

    app = FastAPI(
        title="SDR DTV PoC",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        responses=ERRORS,
    )
    app.add_middleware(Protection, origin=settings.origin, token=token)

    def manager() -> Manager:
        return app.state.manager  # type: ignore[no-any-return]

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"code": str(exc), "message": "Operation unavailable"}, status_code=409)

    @app.exception_handler(Unavailable)
    async def unavailable(request: Request, exc: Unavailable) -> JSONResponse:
        return JSONResponse(
            {"code": str(exc), "message": "Operation unavailable"},
            status_code=501
            if str(exc) in {"live_not_implemented", "saved_scan_not_configured"}
            else 503,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"code": str(exc.detail), "message": "Operation unavailable"},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default response echoes input, which can contain private paths.
        return JSONResponse(
            {"code": "invalid_request", "message": "Check request fields"}, status_code=422
        )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{filename}", include_in_schema=False)
    def ui_file(filename: str) -> FileResponse:
        if filename not in UI_FILES:
            raise HTTPException(404, "file_not_found")
        return FileResponse(STATIC / filename, media_type=UI_FILES[filename])

    @app.get("/static/vendor/{filename}", include_in_schema=False)
    def vendor(filename: str) -> FileResponse:
        if filename not in {
            "vue.runtime.global.prod.js",
            "hls.min.js",
            "vue.LICENSE",
            "hls.LICENSE",
            "Apache-2.0.txt",
            "manifest.json",
        }:
            raise HTTPException(404, "file_not_found")
        return FileResponse(STATIC / "vendor" / filename)

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, object]:
        return {
            "csrf_token": token,
            "mode": "live_backend" if settings.live else "synthetic_backend",
            "live_available": settings.live is not None,
            "hls_available": True,
            "recording_available": True,
            "scan_available": True,
            "scan_presets": {
                "synthetic": [13, 14],
                "uhf": list(range(13, 53)),
                "live": sorted({p.channel for p in settings.live.profiles.values()})
                if settings.live
                else [],
            },
        }

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "live_backend" if settings.live else "synthetic_backend"}

    @app.get("/api/diagnostics", response_model=Diagnostics)
    def diagnostics(input_kind: InputKind = InputKind.synthetic) -> Diagnostics:
        checks = {
            "storage": Diagnostic(
                status="ok"
                if shutil.disk_usage(settings.data_dir).free >= settings.min_free_bytes
                else "failed",
                code="free_space",
            ),
            "demo_source": Diagnostic(
                status="ok" if settings.demo_path.is_file() else "missing", code="synthetic_ts"
            ),
            "ffmpeg": Diagnostic(
                status="ok" if shutil.which("ffmpeg") else "missing",
                code="generator_and_hls",
            ),
            "service_information": Diagnostic(
                status=(
                    "not_required"
                    if input_kind != InputKind.live
                    else "ok"
                    if shutil.which("tstables")
                    else "missing"
                ),
                code="arib_service_names",
            ),
            "board": Diagnostic(
                status="not_required" if input_kind != InputKind.live else "not_checked",
                code="no_device_io",
            ),
            "native_receiver": Diagnostic(status="not_checked", code="separate_runtime_probe"),
            "card": Diagnostic(
                status="not_required" if input_kind == InputKind.synthetic else "not_checked",
                code="no_card_io",
            ),
            "cas": Diagnostic(
                status="not_required" if input_kind == InputKind.synthetic else "not_checked",
                code="external_cas_configured_not_probed"
                if settings.cas_executable
                else "external_cas_not_configured",
            ),
        }
        return Diagnostics(input_kind=input_kind, checks=checks)

    @app.get("/api/recovery")
    async def recovery_status() -> dict[str, object]:
        return manager().recovery.status()

    @app.post("/api/recovery", status_code=202)
    async def recover_receiver() -> dict[str, object]:
        return manager().recovery.start()

    @app.get("/api/services", response_model=list[Service])
    async def services() -> list[Service]:
        return list(manager().scans.services.values())

    @app.post("/api/sessions", status_code=202, response_model=Session)
    async def start(body: SessionStart) -> Session:
        try:
            return await manager().select(body)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except Unavailable as exc:
            raise HTTPException(
                501 if str(exc) == "live_not_implemented" else 503, str(exc)
            ) from None

    @app.get("/api/sessions", response_model=list[Session])
    async def sessions() -> list[Session]:
        return list(manager().sessions.values())

    @app.get("/api/sessions/{session_id}", response_model=Session)
    async def session(session_id: UUID) -> Session:
        if session_id not in manager().sessions:
            raise HTTPException(404, "session_not_found")
        return manager().sessions[session_id]

    @app.post("/api/sessions/{session_id}/stop", status_code=202, response_model=Session)
    async def stop(session_id: UUID) -> Session:
        if session_id not in manager().sessions:
            raise HTTPException(404, "session_not_found")
        return manager().stop(session_id)

    @app.get("/api/artifacts/{artifact_id}", response_model=Artifact)
    async def artifact(artifact_id: UUID) -> Artifact:
        record = manager().store.artifact(str(artifact_id))
        if not record or record[0].partial:
            raise HTTPException(404, "artifact_not_found")
        return record[0]

    @app.get("/api/artifacts/{artifact_id}/download")
    async def download(artifact_id: UUID) -> StreamingResponse:
        record = manager().store.artifact(str(artifact_id))
        if not record or record[0].partial or record[0].kind != "source_ts":
            raise HTTPException(404, "artifact_not_found")
        try:
            file = open_registered(settings.data_dir, record[1])
        except (OSError, ValueError):
            raise HTTPException(404, "artifact_not_found") from None
        return StreamingResponse(
            chunks(file),
            media_type="video/mp2t",
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}.ts"'},
            background=BackgroundTask(file.close),
        )

    @app.get("/api/artifacts/{artifact_id}/files/{filename}")
    async def hls_file(artifact_id: UUID, filename: str) -> Response:
        record = manager().store.artifact(str(artifact_id))
        if (
            not record
            or record[0].partial
            or record[0].kind != "hls"
            or not NAME.fullmatch(filename)
            or filename not in record[0].members
        ):
            raise HTTPException(404, "artifact_not_found")
        relative = str(Path(record[1]).parent / filename)
        try:
            file = open_registered(settings.data_dir, relative)
            if filename.endswith(".m3u8"):
                with file:
                    data = (
                        record[0].playlist_snapshot.encode("utf-8")
                        if record[0].playlist_snapshot is not None
                        else file.read(64 * 1024 + 1)
                    )
                validate_playlist(data, record[0].members)
                return Response(
                    data,
                    media_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"},
                )
        except (OSError, ValueError, UnicodeError):
            raise HTTPException(404, "artifact_not_found") from None
        return StreamingResponse(
            chunks(file), media_type="video/mp2t", background=BackgroundTask(file.close)
        )

    @app.post("/api/scans", status_code=202, response_model=Scan)
    async def scan_start(body: ScanStart) -> Scan:
        return manager().scans.start(body)

    @app.get("/api/scans", response_model=list[Scan])
    async def scans() -> list[Scan]:
        return list(manager().scans.items.values())

    @app.get("/api/scans/{scan_id}", response_model=Scan)
    async def scan_get(scan_id: UUID) -> Scan:
        if scan_id not in manager().scans.items:
            raise HTTPException(404, "scan_not_found")
        return manager().scans.items[scan_id]

    @app.post("/api/scans/{scan_id}/stop", status_code=202, response_model=Scan)
    async def scan_stop(scan_id: UUID) -> Scan:
        await scan_get(scan_id)
        return manager().scans.stop(scan_id)

    @app.post("/api/recordings", status_code=202, response_model=Recording)
    async def recording_start(body: RecordingStart) -> Recording:
        await session(body.session_id)
        return manager().recordings.start(body)

    @app.get("/api/recordings", response_model=list[Recording])
    async def recordings() -> list[Recording]:
        return [
            manager().recordings.get(key)
            for key, record in manager().recordings.items.items()
            if record.deleted_at is None
        ]

    @app.get("/api/recordings/{recording_id}", response_model=Recording)
    async def recording_get(recording_id: UUID) -> Recording:
        if (
            recording_id not in manager().recordings.items
            or manager().recordings.items[recording_id].deleted_at
        ):
            raise HTTPException(404, "recording_not_found")
        return manager().recordings.get(recording_id)

    @app.post("/api/recordings/{recording_id}/delete", response_model=Recording)
    async def recording_delete(recording_id: UUID) -> Recording:
        if recording_id not in manager().recordings.items:
            raise HTTPException(404, "recording_not_found")
        return manager().recordings.delete(recording_id)

    @app.post("/api/recordings/{recording_id}/stop", status_code=202, response_model=Recording)
    async def recording_stop(recording_id: UUID) -> Recording:
        record = await recording_get(recording_id)
        if record.state == State.running:
            manager().recordings.finish(EndReason.requested)
        return record

    @app.post("/api/recordings/{recording_id}/playback", status_code=202, response_model=Playback)
    async def playback_start(recording_id: UUID) -> Playback:
        await recording_get(recording_id)
        return manager().recordings.start_playback(recording_id)

    @app.get("/api/recordings/{recording_id}/playback", response_model=Playback)
    async def recording_playback(recording_id: UUID) -> Playback:
        record = await recording_get(recording_id)
        if record.deletion_pending:
            raise HTTPException(409, "recording_deleted")
        if recording_id not in manager().recordings.playbacks:
            raise HTTPException(404, "playback_not_started")
        return manager().recordings.playbacks[recording_id]

    @app.get("/api/recordings/{recording_id}/download")
    async def recording_download(recording_id: UUID) -> StreamingResponse:
        record = await recording_get(recording_id)
        if record.partial or not record.artifact_id or not record.file_available:
            raise HTTPException(409, "recording_unavailable")
        return await download(record.artifact_id)

    return app
