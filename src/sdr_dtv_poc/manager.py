# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import errno
import fcntl
import hashlib
import shutil
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from .adapter import Adapter, FileAdapter, FileWorkerFailed, LiveAdapter
from .config import Settings, Source
from .device_lock import DeviceBusy, DeviceLock
from .live_config import LiveProfile
from .media import Media
from .models import (
    START_GRACE,
    STOP_GRACE,
    Artifact,
    EndReason,
    InputKind,
    MediaStatus,
    Restore,
    Session,
    SessionStart,
    Stage,
    State,
)
from .recording import Recordings
from .scanning import Scans
from .store import Store

TERMINAL = {State.completed, State.failed, State.interrupted}


class Conflict(Exception):
    pass


class Unavailable(Exception):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


class Manager:
    def __init__(
        self,
        settings: Settings,
        adapter_factory: Callable[[Source], Adapter] = FileAdapter,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.adapter_factory = adapter_factory
        self.clock = clock
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = (settings.data_dir / ".api.lock").open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("another API process owns this data directory") from None
        try:
            self.store = Store(settings.data_dir / "state.sqlite3")
        except Exception:
            self.lock.close()
            raise
        self.sessions: dict[UUID, Session] = {}
        self.requests: dict[UUID, tuple[UUID, str]] = {}
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self.stop_reason = EndReason.requested
        self.active_id: UUID | None = None
        self.storage_failed = False
        self.device = DeviceLock(settings.device_lock_dir or settings.data_dir / "device")
        self.deadlines: dict[UUID, float] = {}
        self.sources: dict[UUID, Source] = {}
        self.selection_lock = asyncio.Lock()
        for session, request in self.store.sessions():
            request = SessionStart.model_validate_json(request).model_dump_json()
            if session.state not in TERMINAL:
                session.state = State.interrupted
                session.partial = True
                session.end_reason = EndReason.server_restart
                session.stage = Stage.cleanup
                session.ended_at = now()
                if session.hls:
                    session.hls.state, session.hls.url = "interrupted", None
                    session.hls.error_code, session.hls.error_stage = "server_restart", "cleanup"
                    if session.hls.artifact_id:
                        entry = self.store.artifact(str(session.hls.artifact_id))
                        if entry:
                            entry[0].partial = True
                            self.store.update_artifact(entry[0])
                if session.input_kind == InputKind.live:
                    session.restore = Restore.unknown
                self.store.save_session(session, request)
            self.sessions[session.id] = session
            self.requests[session.request_id] = (session.id, request)
        self.recordings = Recordings(self)
        self.scans = Scans(self)

    def persist(self, session: Session) -> None:
        try:
            self.store.save_session(session, self.requests[session.request_id][1])
        except sqlite3.Error:
            self.storage_failed = True
            raise

    def start(self, request: SessionStart) -> Session:
        serialized = request.model_dump_json()
        previous = self.requests.get(request.request_id)
        if previous:
            if previous[1] != serialized:
                raise Conflict("request_id_reused")
            return self.sessions[previous[0]]
        self.check_idle()
        if len(self.sessions) >= 1000:
            raise Conflict("session_history_limit")
        service = self.scans.services.get(request.service_key) if request.service_key else None
        if request.service_key and service is None:
            raise Unavailable("service_not_found")
        kind = service.input_kind if service else request.input_kind
        source_id = service.source_id if service else request.source_id
        source = (
            self.live_source(
                source_id,
                LiveProfile.from_service(service.profile)
                if service and "channel" in service.profile
                else None,
            )
            if kind == InputKind.live
            else self.synthetic_source(source_id)
            if kind == InputKind.synthetic
            else self.settings.saved_sources.get(source_id)
        )
        if source is None:
            raise Unavailable("source_not_registered")
        if source.live_id is None and not source.path.is_file():
            raise Unavailable("source_missing")
        if shutil.disk_usage(self.settings.data_dir).free < self.settings.min_free_bytes:
            raise Conflict("storage_full")
        session = Session(
            id=uuid4(),
            request_id=request.request_id,
            input_kind=kind,
            restore=Restore.pending if kind == InputKind.live else Restore.not_required,
            source_id=source_id,
            service=service,
            selected_service_id=service.service_id if service else request.selected_service_id,
            hls=MediaStatus() if request.enable_hls else None,
            state=State.starting,
            duration_seconds=request.duration_seconds,
            started_at=now(),
            deadline_at=(
                datetime.now(UTC) + timedelta(seconds=request.duration_seconds)
            ).isoformat(),
        )
        self.acquire_device()
        try:
            self.store.save_session(session, serialized)
        except sqlite3.Error:
            self.device.release()
            self.storage_failed = True
            raise Unavailable("database_error") from None
        self.sessions[session.id] = session
        self.requests[request.request_id] = (session.id, serialized)
        self.active_id = session.id
        self.stop_event = asyncio.Event()
        self.stop_reason = EndReason.requested
        deadline = self.clock() + request.duration_seconds
        self.deadlines[session.id], self.sources[session.id] = deadline, source
        adapter: Adapter
        if kind == InputKind.live:
            assert self.device.fd is not None
            adapter = LiveAdapter(
                self.settings,
                source,
                self.settings.data_dir / "native" / str(session.id),
                request.duration_seconds,
                self.device.fd,
            )
        else:
            adapter = self.adapter_factory(source)
        if isinstance(adapter, FileAdapter):
            adapter.lock_fd = self.device.fd
        self.task = asyncio.create_task(self.run(session, adapter, deadline))
        return session

    def live_source(self, source_id: str, profile: LiveProfile | None = None) -> Source:
        if not self.settings.live or not self.settings.device_lock_dir:
            raise Unavailable("live_not_configured")
        if profile is None and source_id not in self.settings.live.profiles:
            raise Unavailable("source_not_registered")
        return Source(
            self.settings.live.config_path, 18_000_000, live_id=source_id, live_profile=profile
        )

    def synthetic_source(self, source_id: str) -> Source | None:
        if source_id == "demo":
            return Source(self.settings.demo_path, self.settings.demo_bitrate)
        if source_id == "demo-14":
            return Source(
                self.settings.demo_path.with_name(self.settings.demo_path.stem + "-14.ts"),
                self.settings.demo_bitrate,
            )
        return None

    def check_idle(self) -> None:
        if self.storage_failed:
            raise Unavailable("database_error")
        if any(
            s.restore in {Restore.pending, Restore.unknown, Restore.failed}
            for s in self.sessions.values()
        ):
            raise Conflict("restore_unverified")
        if self.recordings.active:
            raise Conflict("recording_busy")
        if self.task and not self.task.done():
            raise Conflict("session_busy")
        if self.scans.task and not self.scans.task.done():
            raise Conflict("scan_busy")

    def acquire_device(self) -> None:
        try:
            self.device.acquire()
        except DeviceBusy as exc:
            raise Conflict(str(exc)) from None

    def remaining(self, session: Session) -> float:
        source = self.sources[session.id]
        if source.live_id is not None:
            return self.deadlines[session.id] - self.clock()
        source_remaining = (
            max(0, source.path.stat().st_size - session.bytes_received) * 8 / source.bitrate
        )
        return min(self.deadlines[session.id] - self.clock(), source_remaining)

    async def select(self, request: SessionStart) -> Session:
        async with self.selection_lock:
            if request.request_id in self.requests:
                return self.start(request)
            if request.service_key:
                if request.service_key not in self.scans.services:
                    raise Unavailable("service_not_found")
                if self.recordings.active:
                    raise Conflict("recording_busy")
                if self.task and not self.task.done() and self.active_id:
                    self.stop(self.active_id)
                    await self.task
            return self.start(request)

    def stop(self, session_id: UUID) -> Session:
        session = self.sessions[session_id]
        if session.state not in TERMINAL:
            session.state = State.stopping
            self.stop_event.set()
            try:
                self.persist(session)
            except sqlite3.Error:
                self.storage_failed = True
        return session

    async def run(self, session: Session, adapter: Adapter, deadline: float) -> None:
        directory = self.settings.data_dir / "sessions" / str(session.id)
        path = directory / "stream.partial"
        reason = EndReason.worker_failed
        pending = b""
        digest = hashlib.sha256()
        media = (
            Media(
                self.settings,
                self.store,
                session.id,
                session.selected_service_id,
                session.hls,
                lambda: self.persist(session),
            )
            if session.hls
            else None
        )
        read_task: asyncio.Task[bytes] | None = None
        stop_task: asyncio.Task[bool] | None = None
        last_metrics = 0.0
        try:
            directory.mkdir(parents=True)
            await asyncio.wait_for(adapter.start(), min(START_GRACE, session.duration_seconds))
            if media:
                await media.start()
            session.state = State.stopping if self.stop_event.is_set() else State.running
            session.stage = Stage.transport
            self.persist(session)
            startup_deadline = min(deadline, self.clock() + START_GRACE)
            stop_task = asyncio.create_task(self.stop_event.wait())
            with path.open("xb") as output:
                while True:
                    remaining = deadline - self.clock()
                    if self.stop_event.is_set():
                        reason = self.stop_reason
                        break
                    if remaining <= 0:
                        reason = EndReason.deadline
                        break
                    read_task = asyncio.create_task(adapter.read())
                    timeout = remaining
                    if session.bytes_received == 0:
                        timeout = min(timeout, max(0, startup_deadline - self.clock()))
                    done, _ = await asyncio.wait(
                        [read_task, stop_task],
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_task in done:
                        reason = self.stop_reason
                        break
                    if read_task not in done:
                        reason = (
                            EndReason.deadline
                            if self.clock() >= deadline
                            else EndReason.startup_timeout
                        )
                        break
                    data = read_task.result()
                    if not data:
                        if pending or not session.bytes_received:
                            raise RuntimeError("invalid TS framing")
                        reason = EndReason.eof
                        break
                    pending += data
                    count = len(pending) // 188 * 188
                    data, pending = pending[:count], pending[count:]
                    if any(data[n] != 0x47 for n in range(0, len(data), 188)):
                        raise RuntimeError("invalid TS framing")
                    if session.bytes_received + len(data) > self.settings.max_output_bytes:
                        reason = EndReason.output_limit
                        break
                    if shutil.disk_usage(directory).free < self.settings.min_free_bytes:
                        reason = EndReason.storage_full
                        break
                    session.stage = Stage.storage
                    output.write(data)
                    self.recordings.write(data)
                    if media:
                        media.offer(data)
                    if data and session.ts_started_at is None:
                        session.ts_started_at = now()
                    digest.update(data)
                    session.bytes_received += len(data)
                    if isinstance(adapter, LiveAdapter) and self.clock() - last_metrics >= 1:
                        session.receiver_metrics = adapter.metrics()
                        last_metrics = self.clock()
                    session.stage = Stage.transport
        except TimeoutError:
            reason = EndReason.startup_timeout
        except sqlite3.Error:
            reason = EndReason.database_error
            self.storage_failed = True
        except OSError as exc:
            reason = (
                EndReason.storage_full if exc.errno == errno.ENOSPC else EndReason.worker_failed
            )
        except Exception:
            # Deliberately keep subprocess/configuration details out of HTTP and logs.
            reason = EndReason.worker_failed
        finally:
            if isinstance(adapter, LiveAdapter):
                adapter.request_stop()
            for task in (read_task, stop_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (read_task, stop_task) if t), return_exceptions=True)
            if self.recordings.active:
                self.recordings.finish(
                    EndReason.server_shutdown
                    if reason == EndReason.server_shutdown
                    else EndReason.source_ended
                )
            if media:
                try:
                    await media.close()
                except sqlite3.Error:
                    self.storage_failed = True
            failure_stage = session.stage
            session.stage = Stage.cleanup
            try:
                session.restore = await asyncio.wait_for(
                    adapter.stop(), 23 if isinstance(adapter, LiveAdapter) else STOP_GRACE
                )
                if isinstance(adapter, LiveAdapter) and adapter.failed:
                    reason = EndReason.worker_failed
                if isinstance(adapter, LiveAdapter):
                    session.receiver_metrics = adapter.metrics()
            except FileWorkerFailed:
                session.restore = Restore.not_required
                # Preserve an earlier input/storage failure and its diagnostic stage.
                if reason in {EndReason.eof, EndReason.requested, EndReason.deadline}:
                    reason = EndReason.worker_failed
                    failure_stage = Stage.cleanup
            except Exception:
                session.restore = Restore.unknown
            if session.restore in {Restore.pending, Restore.unknown, Restore.failed}:
                try:
                    self.device.block(session.id)
                except OSError:
                    self.storage_failed = True
            if self.storage_failed:
                reason = EndReason.database_error
            success = reason in {EndReason.eof, EndReason.requested, EndReason.deadline}
            success = success and session.restore in {Restore.not_required, Restore.verified}
            session.end_reason = reason
            session.ended_at = now()
            session.error_stage = (
                None
                if success
                else (
                    Stage.cleanup
                    if session.restore in {Restore.unknown, Restore.failed}
                    else Stage.storage
                    if reason
                    in {EndReason.storage_full, EndReason.output_limit, EndReason.database_error}
                    else failure_stage
                )
            )
            session.partial = not success
            session.state = State.completed if success else State.failed
            completed_artifact: tuple[Artifact, str] | None = None
            if success and session.bytes_received:
                try:
                    final = path.with_name("stream.ts")
                    path.rename(final)
                    artifact = Artifact(
                        id=uuid4(),
                        session_id=session.id,
                        kind="source_ts",
                        media_type="video/mp2t",
                        size_bytes=session.bytes_received,
                        sha256=digest.hexdigest(),
                    )
                    completed_artifact = (artifact, str(final.relative_to(self.settings.data_dir)))
                    session.artifact_id = artifact.id
                except OSError:
                    session.partial = True
                    session.state = State.failed
                    session.end_reason = EndReason.worker_failed
                    session.error_stage = Stage.storage
            session.stage = Stage.complete if session.state == State.completed else Stage.cleanup
            try:
                self.store.finish(session, completed_artifact)
            except sqlite3.Error:
                self.storage_failed = True
                session.state = State.failed
                session.partial = True
                session.artifact_id = None
                session.end_reason = EndReason.database_error
                session.stage = Stage.storage
                session.error_stage = Stage.storage
                # If the disk is still unavailable, old running metadata is recovered
                # as interrupted on restart. Never expose an uncommitted artifact.
                try:
                    self.persist(session)
                except sqlite3.Error:
                    pass
            finally:
                self.active_id = None
                self.device.release()

    async def close(self) -> None:
        try:
            if self.task and not self.task.done():
                self.stop_reason = EndReason.server_shutdown
                self.stop_event.set()
                await self.task
            await self.scans.close()
            await self.recordings.close()
        finally:
            self.device.release()
            self.store.close()
            self.lock.close()
