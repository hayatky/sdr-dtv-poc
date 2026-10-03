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

from .adapter import Adapter, FileAdapter, FileWorkerFailed
from .config import Settings, Source
from .models import (
    START_GRACE,
    STOP_GRACE,
    Artifact,
    EndReason,
    InputKind,
    Restore,
    Session,
    SessionStart,
    Stage,
    State,
)
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
        for session, request in self.store.sessions():
            if session.state not in TERMINAL:
                session.state = State.interrupted
                session.partial = True
                session.end_reason = EndReason.server_restart
                session.stage = Stage.cleanup
                session.ended_at = now()
                if session.input_kind == InputKind.live:
                    session.restore = Restore.unknown
                self.store.save_session(session, request)
            self.sessions[session.id] = session
            self.requests[session.request_id] = (session.id, request)

    def persist(self, session: Session) -> None:
        self.store.save_session(session, self.requests[session.request_id][1])

    def start(self, request: SessionStart) -> Session:
        serialized = request.model_dump_json()
        previous = self.requests.get(request.request_id)
        if previous:
            if previous[1] != serialized:
                raise Conflict("request_id_reused")
            return self.sessions[previous[0]]
        if self.storage_failed:
            raise Unavailable("database_error")
        if any(s.restore in {Restore.unknown, Restore.failed} for s in self.sessions.values()):
            raise Conflict("restore_unverified")
        if self.task and not self.task.done():
            raise Conflict("session_busy")
        if len(self.sessions) >= 1000:
            raise Conflict("session_history_limit")
        if request.input_kind == InputKind.live:
            raise Unavailable("live_not_implemented")
        if request.input_kind == InputKind.synthetic:
            if request.source_id != "demo":
                raise Unavailable("source_not_registered")
            source = Source(self.settings.demo_path, self.settings.demo_bitrate)
        else:
            if request.source_id not in self.settings.saved_sources:
                raise Unavailable("source_not_registered")
            source = self.settings.saved_sources[request.source_id]
        if not source.path.is_file():
            raise Unavailable("source_missing")
        if shutil.disk_usage(self.settings.data_dir).free < self.settings.min_free_bytes:
            raise Conflict("storage_full")
        session = Session(
            id=uuid4(),
            request_id=request.request_id,
            input_kind=request.input_kind,
            source_id=request.source_id,
            state=State.starting,
            duration_seconds=request.duration_seconds,
            started_at=now(),
            deadline_at=(
                datetime.now(UTC) + timedelta(seconds=request.duration_seconds)
            ).isoformat(),
        )
        try:
            self.store.save_session(session, serialized)
        except sqlite3.Error:
            self.storage_failed = True
            raise Unavailable("database_error") from None
        self.sessions[session.id] = session
        self.requests[request.request_id] = (session.id, serialized)
        self.active_id = session.id
        self.stop_event = asyncio.Event()
        self.stop_reason = EndReason.requested
        deadline = self.clock() + request.duration_seconds
        self.task = asyncio.create_task(self.run(session, self.adapter_factory(source), deadline))
        return session

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
        read_task: asyncio.Task[bytes] | None = None
        stop_task: asyncio.Task[bool] | None = None
        try:
            directory.mkdir(parents=True)
            await asyncio.wait_for(adapter.start(), min(START_GRACE, session.duration_seconds))
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
                    digest.update(data)
                    session.bytes_received += len(data)
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
            for task in (read_task, stop_task):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (read_task, stop_task) if t), return_exceptions=True)
            failure_stage = session.stage
            session.stage = Stage.cleanup
            try:
                session.restore = await asyncio.wait_for(adapter.stop(), STOP_GRACE)
            except FileWorkerFailed:
                session.restore = Restore.not_required
                # Preserve an earlier input/storage failure and its diagnostic stage.
                if reason in {EndReason.eof, EndReason.requested, EndReason.deadline}:
                    reason = EndReason.worker_failed
                    failure_stage = Stage.cleanup
            except Exception:
                session.restore = Restore.unknown
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

    async def close(self) -> None:
        try:
            if self.task and not self.task.done():
                self.stop_reason = EndReason.server_shutdown
                self.stop_event.set()
                await self.task
        finally:
            self.store.close()
            self.lock.close()
