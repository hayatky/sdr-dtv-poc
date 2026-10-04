# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import errno
import hashlib
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, BinaryIO
from uuid import UUID, uuid4

from .artifacts import open_registered, remove_owned_directory
from .media import ENCODING_PROFILE, Media, timestamp
from .models import STOP_GRACE, Artifact, EndReason, Playback, Recording, RecordingStart, State

# HD conversion stays finite while allowing concurrent live encoding on the
# bounded CPU allocation. Playback begins after the VOD playlist is finalized.
PLAYBACK_LIMIT = 600

if TYPE_CHECKING:
    from .manager import Manager


class Recordings:
    def __init__(self, manager: "Manager"):
        self.manager = manager
        self.items: dict[UUID, Recording] = {}
        self.requests: dict[UUID, tuple[UUID, str]] = {}
        self.active: Recording | None = None
        self.file: BinaryIO | None = None
        self.started = 0.0
        self.deadline = 0.0
        self.digest = hashlib.sha256()
        self.timer: asyncio.Task[None] | None = None
        self.playbacks: dict[UUID, Playback] = {}
        self.playback_task: asyncio.Task[None] | None = None
        self.playback_owner: UUID | None = None
        self.media: Media | None = None
        for body, request in manager.store.records("recordings"):
            record = Recording.model_validate_json(body)
            if record.state in {State.starting, State.running, State.stopping}:
                record.state, record.partial = State.interrupted, True
                record.end_reason, record.ended_at = EndReason.server_restart, timestamp()
                try:
                    with open_registered(
                        manager.settings.data_dir, f"recordings/{record.id}/original.partial"
                    ) as file:
                        record.bytes_written = os.fstat(file.fileno()).st_size
                except (OSError, ValueError):
                    pass
                manager.store.save_record("recordings", record)
            self.items[record.id] = record
            self.requests[record.request_id] = (record.id, request)
        for body, _ in manager.store.records("playbacks"):
            playback = Playback.model_validate_json(body)
            if playback.state not in {"completed", "failed", "interrupted"}:
                if playback.artifact_id:
                    entry = manager.store.artifact(str(playback.artifact_id))
                    if entry:
                        entry[0].partial = True
                        manager.store.update_artifact(entry[0])
                playback.state = "interrupted"
                playback.error_code, playback.error_stage = "server_restart", "cleanup"
                playback.url, playback.ended_at = None, timestamp()
                manager.store.save_record("playbacks", playback)
            self.playbacks[playback.recording_id] = playback

    def start(self, request: RecordingStart) -> Recording:
        from .manager import Conflict, Unavailable

        m = self.manager
        previous = self.requests.get(request.request_id)
        if previous:
            if previous[1] != request.model_dump_json():
                raise Conflict("request_id_reused")
            return self.items[previous[0]]
        if m.storage_failed:
            raise Unavailable("database_error")
        if self.active:
            raise Conflict("recording_busy")
        if sum(r.deleted_at is None for r in self.items.values()) >= 1000:
            raise Conflict("recording_history_limit")
        session = m.sessions[request.session_id]
        if session.state != State.running or not session.bytes_received:
            raise Conflict("session_not_running")
        if m.remaining(session) < request.duration_seconds + STOP_GRACE:
            raise Conflict("insufficient_session_time")
        needed = int(m.sources[session.id].bitrate * request.duration_seconds / 8)
        if needed > m.settings.max_recording_bytes:
            raise Conflict("recording_output_limit")
        # Live demodulation also retains its native TS alongside session/recording.
        copies = 3 if m.sources[session.id].live_id is not None else 2
        # Include bounded media growth on this filesystem as well.
        media_growth = m.settings.max_hls_bytes if session.hls else 0
        if self.playback_task and not self.playback_task.done():
            media_growth += m.settings.max_playback_bytes
        if (
            shutil.disk_usage(m.settings.data_dir).free
            < m.settings.min_free_bytes + copies * needed + media_growth
        ):
            raise Conflict("storage_full")
        record = Recording(
            id=uuid4(),
            request_id=request.request_id,
            session_id=session.id,
            input_kind=session.input_kind,
            service=session.service,
            selected_service_id=session.selected_service_id,
            started_at=timestamp(),
            deadline_at=(
                datetime.now(UTC) + timedelta(seconds=request.duration_seconds)
            ).isoformat(),
            duration_seconds=request.duration_seconds,
            source_offset_bytes=session.bytes_received,
        )
        directory = m.settings.data_dir / "recordings" / str(record.id)
        try:
            directory.mkdir(parents=True)
            file = (directory / "original.partial").open("xb")
            try:
                m.store.save_record("recordings", record, request)
            except Exception:
                file.close()
                raise
        except sqlite3.Error:
            m.storage_failed = True
            raise Unavailable("database_error") from None
        except OSError:
            raise Unavailable("recording_write_failed") from None
        self.started = m.clock()
        self.deadline = self.started + request.duration_seconds
        self.items[record.id] = record
        self.requests[request.request_id] = (record.id, request.model_dump_json())
        self.active, self.file, self.digest = record, file, hashlib.sha256()
        self.timer = asyncio.create_task(self.watch_deadline())
        return record

    async def watch_deadline(self) -> None:
        while self.active:
            if self.manager.clock() >= self.deadline:
                self.finish(EndReason.deadline)
                return
            await asyncio.sleep(0.05)

    def write(self, data: bytes) -> None:
        record, file = self.active, self.file
        if record is None or file is None:
            return
        if self.manager.clock() >= self.deadline:
            self.finish(EndReason.deadline)
            return
        if record.bytes_written + len(data) > self.manager.settings.max_recording_bytes:
            self.finish(EndReason.output_limit)
            return
        try:
            if (
                shutil.disk_usage(self.manager.settings.data_dir).free
                < self.manager.settings.min_free_bytes
            ):
                self.finish(EndReason.storage_full)
                return
            written = file.write(data)
            if written != len(data):
                raise OSError("short write")
            self.digest.update(data)
            record.bytes_written += len(data)
            record.elapsed_seconds = min(
                record.duration_seconds, self.manager.clock() - self.started
            )
        except OSError as exc:
            self.finish(
                EndReason.storage_full if exc.errno == errno.ENOSPC else EndReason.worker_failed
            )

    def finish(self, reason: EndReason) -> None:
        record, file = self.active, self.file
        if record is None or file is None:
            return
        self.active, self.file = None, None
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        success = reason in {EndReason.requested, EndReason.deadline} and record.bytes_written > 0
        artifact_entry = None
        path = self.manager.settings.data_dir / "recordings" / str(record.id) / "original.partial"
        try:
            file.flush()
            os.fsync(file.fileno())
            file.close()
            if success:
                final = path.with_suffix(".ts")
                path.rename(final)
                artifact = Artifact(
                    id=uuid4(),
                    session_id=record.session_id,
                    kind="source_ts",
                    media_type="video/mp2t",
                    size_bytes=record.bytes_written,
                    sha256=self.digest.hexdigest(),
                )
                artifact_entry = (artifact, str(final.relative_to(self.manager.settings.data_dir)))
                record.artifact_id = artifact.id
                record.download_url = f"/api/recordings/{record.id}/download"
                record.file_available = True
        except OSError as exc:
            success, reason = (
                False,
                (EndReason.storage_full if exc.errno == errno.ENOSPC else EndReason.worker_failed),
            )
        finally:
            try:
                file.close()
            except OSError as exc:
                success, reason = (
                    False,
                    (
                        EndReason.storage_full
                        if exc.errno == errno.ENOSPC
                        else EndReason.worker_failed
                    ),
                )
        if not success:
            artifact_entry = None
            record.artifact_id, record.download_url, record.file_available = None, None, False
        record.state = State.completed if success else State.failed
        record.partial = not success
        record.end_reason, record.ended_at = reason, timestamp()
        record.elapsed_seconds = max(0, self.manager.clock() - self.started)
        try:
            self.manager.store.save_record("recordings", record, artifact=artifact_entry)
        except sqlite3.Error:
            self.manager.storage_failed = True
            record.state, record.partial = State.failed, True
            record.end_reason, record.artifact_id = EndReason.database_error, None
            record.download_url, record.file_available = None, False
            try:
                self.manager.store.save_record("recordings", record)
            except sqlite3.Error:
                pass

    def get(self, recording_id: UUID) -> Recording:
        record = self.items[recording_id]
        record.file_available = False
        if record.deleted_at or record.deletion_pending:
            record.download_url = None
            return record
        if record.artifact_id:
            artifact = self.manager.store.artifact(str(record.artifact_id))
            if artifact:
                try:
                    with open_registered(self.manager.settings.data_dir, artifact[1]) as file:
                        record.file_available = (
                            os.fstat(file.fileno()).st_size == record.bytes_written
                        )
                except (OSError, ValueError):
                    pass
        return record

    def delete(self, recording_id: UUID) -> Recording:
        from .manager import Conflict, Unavailable

        record = self.items[recording_id]
        if record.deleted_at:
            return record
        if record.state in {State.starting, State.running, State.stopping}:
            raise Conflict("recording_busy")
        previous = self.playbacks.get(recording_id)
        if (previous and previous.state not in {"completed", "failed", "interrupted"}) or (
            self.playback_owner == recording_id
            and self.playback_task is not None
            and not self.playback_task.done()
        ):
            raise Conflict("playback_busy")
        # Include older encoding profiles, but preserve the session/native TS.
        playbacks = [
            p
            for body, _ in self.manager.store.records("playbacks")
            if (p := Playback.model_validate_json(body)).recording_id == recording_id
        ]
        artifacts = [record.artifact_id, *(p.artifact_id for p in playbacks)]
        pending = record.model_copy(
            update={"deletion_pending": True, "file_available": False, "download_url": None}
        )
        try:
            # Revoke all URLs atomically before removing any files. A failed
            # cleanup stays visible for retry, including after a server restart.
            self.manager.store.save_record(
                "recordings",
                pending,
                revoke_artifact_ids=tuple(str(a) for a in artifacts if a is not None),
            )
        except sqlite3.Error:
            self.manager.storage_failed = True
            raise Unavailable("database_error") from None
        self.items[recording_id] = pending
        try:
            for playback in playbacks:
                remove_owned_directory(self.manager.settings.data_dir, "playback", playback.id)
            remove_owned_directory(self.manager.settings.data_dir, "recordings", record.id)
        except OSError:
            raise Unavailable("recording_delete_failed") from None
        deleted = pending.model_copy(update={"deletion_pending": False, "deleted_at": timestamp()})
        try:
            self.manager.store.save_record("recordings", deleted)
        except sqlite3.Error:
            self.manager.storage_failed = True
            raise Unavailable("database_error") from None
        self.items[recording_id] = deleted
        return deleted

    def start_playback(self, recording_id: UUID) -> Playback:
        from .manager import Conflict, Unavailable

        record = self.get(recording_id)
        if record.deleted_at or record.deletion_pending:
            raise Conflict("recording_deleted")
        previous = self.playbacks.get(recording_id)
        if previous and previous.encoding_profile == ENCODING_PROFILE:
            return previous
        if self.manager.storage_failed:
            raise Unavailable("database_error")
        if record.state != State.completed or record.partial:
            raise Conflict("recording_incomplete")
        if not record.file_available:
            raise Conflict("recording_file_missing")
        if self.playback_task and not self.playback_task.done():
            raise Conflict("playback_busy")
        recording_growth = 0
        if self.active:
            session = self.manager.sessions[self.active.session_id]
            remaining = max(0.0, self.deadline - self.manager.clock())
            source = self.manager.sources[session.id]
            copies = 3 if source.live_id is not None else 2
            recording_growth = int(source.bitrate * remaining / 8) * copies
            if session.hls:
                recording_growth += self.manager.settings.max_hls_bytes
        if (
            shutil.disk_usage(self.manager.settings.data_dir).free
            < self.manager.settings.min_free_bytes
            + self.manager.settings.max_playback_bytes
            + recording_growth
        ):
            raise Conflict("storage_full")
        playback = Playback(
            encoding_profile=ENCODING_PROFILE,
            id=uuid4(),
            recording_id=record.id,
            started_at=timestamp(),
            deadline_at=(datetime.now(UTC) + timedelta(seconds=PLAYBACK_LIMIT)).isoformat(),
        )
        try:
            self.manager.store.save_record("playbacks", playback)
        except sqlite3.Error:
            self.manager.storage_failed = True
            raise Unavailable("database_error") from None
        self.playbacks[record.id] = playback
        self.playback_owner = record.id
        self.playback_task = asyncio.create_task(self.convert(record, playback))
        return playback

    async def convert(self, record: Recording, playback: Playback) -> None:
        m = self.manager

        def changed() -> None:
            m.store.save_record("playbacks", playback)

        media = Media(
            m.settings,
            m.store,
            playback.id,
            record.selected_service_id,
            playback,
            changed,
            vod=True,
        )
        self.media = media
        try:
            async with asyncio.timeout(PLAYBACK_LIMIT):
                entry = m.store.artifact(str(record.artifact_id))
                assert entry
                await media.start()
                with open_registered(m.settings.data_dir, entry[1]) as file:
                    while data := file.read(188 * 7):
                        if media.status.state == "failed":
                            break
                        # Offline conversion may wait, but cannot grow an unbounded queue.
                        while media.queue.full() and media.status.state != "failed":
                            await asyncio.sleep(0.01)
                        media.offer(data)
                        await asyncio.sleep(0)
                await media.close()
        except TimeoutError:
            media.fail("playback_deadline", "conversion")
        except asyncio.CancelledError:
            media.fail("server_shutdown", "cleanup")
        except Exception:
            media.fail("playback_failed", "conversion")
        finally:
            try:
                await media.close()
            except sqlite3.Error:
                m.storage_failed = True
                playback.state, playback.error_code = "failed", "database_error"
                playback.error_stage, playback.url = "storage", None
            playback.ended_at = timestamp()
            try:
                changed()
            except sqlite3.Error:
                m.storage_failed = True
                playback.state, playback.error_code = "failed", "database_error"
                playback.error_stage, playback.url = "storage", None
            self.media = None

    async def close(self) -> None:
        self.finish(EndReason.server_shutdown)
        if self.timer:
            self.timer.cancel()
            await asyncio.gather(self.timer, return_exceptions=True)
        if self.playback_task and not self.playback_task.done():
            self.playback_task.cancel()
            await self.playback_task
