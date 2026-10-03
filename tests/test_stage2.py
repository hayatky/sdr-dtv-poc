# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import hashlib
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from sdr_dtv_poc.config import Settings
from sdr_dtv_poc.device_lock import DeviceBusy, DeviceLock
from sdr_dtv_poc.manager import Conflict, Manager
from sdr_dtv_poc.media import Media
from sdr_dtv_poc.models import (
    Artifact,
    EndReason,
    InputKind,
    MediaStatus,
    Playback,
    RecordingStart,
    Restore,
    ScanStart,
    Service,
    SessionStart,
    State,
)
from sdr_dtv_poc.scanning import frequency

PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184


class Paced:
    def __init__(self, restore: Restore = Restore.not_required):
        self.stopped = False
        self.restore = restore

    async def start(self) -> None:
        pass

    async def read(self) -> bytes:
        await asyncio.sleep(0.001)
        return PACKET * 7

    async def stop(self) -> Restore:
        self.stopped = True
        return self.restore


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    source = tmp_path / "input.ts"
    source.write_bytes(PACKET * 10000)
    return Settings(data_dir=tmp_path / "app", demo_path=source, demo_bitrate=1504)


async def ready(manager: Manager) -> Any:
    session = manager.start(
        SessionStart(request_id=uuid4(), duration_seconds=600, enable_hls=False)
    )
    async with asyncio.timeout(3):
        while not session.bytes_received:
            await asyncio.sleep(0.005)
    return session


def test_recording_clock_conflicts_and_original(settings: Settings) -> None:
    async def run() -> None:
        tick = 0.0
        adapter = Paced()
        m = Manager(settings, adapter_factory=lambda _: adapter, clock=lambda: tick)
        try:
            session = await ready(m)
            request = RecordingStart(request_id=uuid4(), session_id=session.id)
            record = m.recordings.start(request)
            assert m.recordings.start(request) is record
            for operation in (
                lambda: m.recordings.start(
                    RecordingStart(request_id=uuid4(), session_id=session.id)
                ),
                lambda: m.start(SessionStart(request_id=uuid4())),
                lambda: m.scans.start(ScanStart(request_id=uuid4())),
            ):
                with pytest.raises(Conflict, match="recording_busy"):
                    operation()
            await asyncio.sleep(0.01)
            tick = 299.9
            await asyncio.sleep(0.01)
            assert record.state == State.running
            tick = 300.0
            await asyncio.sleep(0.06)
            assert str(record.state) == "completed" and record.end_reason == EndReason.deadline
            assert record.elapsed_seconds == 300
            assert record.bytes_written % 188 == 0
            artifact = m.store.artifact(str(record.artifact_id))
            assert artifact
            data = (settings.data_dir / artifact[1]).read_bytes()
            assert data == PACKET * (len(data) // 188)
            assert hashlib.sha256(data).hexdigest() == artifact[0].sha256
            assert session.state == State.running and not adapter.stopped
            with pytest.raises(Conflict, match="insufficient_session_time"):
                m.recordings.start(RecordingStart(request_id=uuid4(), session_id=session.id))
            r2 = m.recordings.start(
                RecordingStart(request_id=uuid4(), session_id=session.id, duration_seconds=5)
            )
            await asyncio.sleep(0.01)
            m.recordings.finish(EndReason.requested)
            m.recordings.finish(EndReason.requested)
            assert r2.state == State.completed
            assert session.state == State.running
        finally:
            await m.close()
        reopened = Manager(settings)
        assert reopened.recordings.get(record.id).state == State.completed
        assert reopened.recordings.get(record.id).file_available
        assert reopened.task is None
        await reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["shutdown", "source_stop", "write", "space", "db"])
def test_recording_partial_and_database_atomicity(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        m = Manager(settings, adapter_factory=lambda _: Paced())
        try:
            session = await ready(m)
            r = m.recordings.start(
                RecordingStart(request_id=uuid4(), session_id=session.id, duration_seconds=5)
            )
            await asyncio.sleep(0.01)
            if failure == "write":
                assert m.recordings.file
                m.recordings.file.close()

                # Simulate a failing regular-file writer without revealing paths.
                class FailedWriter:
                    def write(self, data: bytes) -> int:
                        raise OSError("injected")

                    def flush(self) -> None:
                        raise OSError("injected")

                    def close(self) -> None:
                        raise OSError("injected close failure")

                m.recordings.file = FailedWriter()  # type: ignore[assignment]
                m.recordings.write(PACKET)
            elif failure == "space":
                usage = type("Usage", (), {"free": 0})()
                monkeypatch.setattr("sdr_dtv_poc.recording.shutil.disk_usage", lambda _: usage)
                m.recordings.write(PACKET)
            elif failure == "db":
                m.store.db.execute(
                    "CREATE TRIGGER fail_recording BEFORE UPDATE ON recordings WHEN json_extract(NEW.body, '$.state')='completed' BEGIN SELECT RAISE(ABORT, 'injected'); END"
                )
                m.recordings.finish(EndReason.requested)
            elif failure == "source_stop":
                m.stop(session.id)
                assert m.task
                await m.task
            else:
                await m.close()
            assert r.state == State.failed and r.partial
            assert r.artifact_id is None
            if failure == "db":
                assert m.storage_failed
                assert m.store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
                assert r.end_reason == EndReason.database_error
        finally:
            if failure != "shutdown":
                await m.close()

    asyncio.run(run())


def test_restart_recording_recovery_and_migration(settings: Settings) -> None:
    async def run() -> None:
        m = Manager(settings, adapter_factory=lambda _: Paced())
        session = await ready(m)
        record = m.recordings.start(
            RecordingStart(request_id=uuid4(), session_id=session.id, duration_seconds=5)
        )
        await m.close()
        # Persist the metadata that would be left by abrupt API death.
        m = Manager(settings)
        record.state, record.partial = State.running, False
        m.store.save_record("recordings", record)
        await m.close()
        m = Manager(settings)
        recovered = m.recordings.items[record.id]
        assert recovered.state == State.interrupted and recovered.partial
        assert recovered.end_reason == EndReason.server_restart
        assert m.recordings.active is None and m.task is None
        await m.close()
        with sqlite3.connect(settings.data_dir / "state.sqlite3") as db:
            db.execute("PRAGMA user_version=1")
        m = Manager(settings)
        assert (settings.data_dir / "state.before-v2.sqlite3").is_file()
        assert m.store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        await m.close()

    asyncio.run(run())


def test_shared_device_lock_process_and_restore_gate(tmp_path: Path) -> None:
    lock = DeviceLock(tmp_path)
    lock.acquire()
    assert lock.fd is not None
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        pass_fds=(lock.fd,),
    )
    lock.release()
    try:
        with pytest.raises(DeviceBusy, match="device_busy"):
            DeviceLock(tmp_path).acquire()
    finally:
        child.communicate(timeout=2)
    lock.acquire()
    lock.block(uuid4())
    lock.release()
    with pytest.raises(DeviceBusy, match="restore_unverified"):
        lock.acquire()


def test_cross_data_directory_conflict_and_restore(settings: Settings) -> None:
    async def run() -> None:
        shared = settings.data_dir / "shared-lock"
        a = Manager(
            replace(settings, device_lock_dir=shared),
            adapter_factory=lambda _: Paced(Restore.unknown),
        )
        b = Manager(replace(settings, data_dir=settings.data_dir / "other", device_lock_dir=shared))
        try:
            s = await ready(a)
            with pytest.raises(Conflict, match="device_busy"):
                b.start(SessionStart(request_id=uuid4()))
            a.stop(s.id)
            assert a.task
            await a.task
            with pytest.raises(Conflict, match="restore_unverified"):
                b.scans.start(ScanStart(request_id=uuid4()))
            assert s.partial and s.restore == Restore.unknown
        finally:
            await a.close()
            await b.close()

    asyncio.run(run())


def test_scan_preserves_identity_unknown_and_cancel(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(
        source: Any, channel: int, source_id: str, kind: InputKind
    ) -> list[Service]:
        await asyncio.sleep(0.01)
        return [
            Service(
                id=f"channel-{channel}",
                name=None if channel == 14 else "Synthetic",
                input_kind=kind,
                physical_channel=channel,
                source_id=source_id,
                service_id=1,
                transport_stream_id=channel,
                detection_stage="ts_si",
            )
        ]

    monkeypatch.setattr("sdr_dtv_poc.scanning.probe", fake_probe)
    settings.demo_path.with_name("input-14.ts").write_bytes(PACKET)

    async def run() -> None:
        m = Manager(settings, adapter_factory=lambda _: Paced())
        try:
            request = ScanStart(request_id=uuid4(), channels=[13, 14, 15])
            scan = m.scans.start(request)
            assert m.scans.start(request) is scan
            with pytest.raises(Conflict, match="scan_busy"):
                m.start(SessionStart(request_id=uuid4()))
            assert m.scans.task
            await m.scans.task
            assert scan.completed_channels == 3 and scan.results[-1].state == "not_detected"
            assert len(m.scans.services) == 2 and m.scans.services["channel-14"].name is None
            m.scans.start(ScanStart(request_id=uuid4(), channels=[15]))
            await m.scans.task
            assert len(m.scans.services) == 2
            cancelled = m.scans.start(ScanStart(request_id=uuid4()))
            m.scans.stop(cancelled.id)
            await m.scans.task
            assert cancelled.end_reason == EndReason.requested
            assert all(r.state == "not_run" for r in cancelled.results)
            assert len(m.scans.services) == 2
            s = await m.select(
                SessionStart(request_id=uuid4(), service_key="channel-13", enable_hls=False)
            )
            changed = await m.select(
                SessionStart(request_id=uuid4(), service_key="channel-14", enable_hls=False)
            )
            assert s.state == State.completed and changed.id != s.id
            assert changed.service and changed.service.physical_channel == 14
        finally:
            await m.close()
        m = Manager(settings)
        assert len(m.scans.services) == 2
        await m.close()

    asyncio.run(run())
    assert frequency(13) == 473142857 and frequency(52) == 707142857
    with pytest.raises(ValueError):
        frequency(53)
    with pytest.raises(ValueError):
        ScanStart(request_id=uuid4(), channels=[13, 13])


def test_media_queue_cas_and_retention(settings: Settings) -> None:
    async def run() -> None:
        m = Manager(settings)
        try:
            status = MediaStatus()
            media = Media(
                replace(settings, media_queue_chunks=1), m.store, uuid4(), 1, status, lambda: None
            )
            media.offer(PACKET)
            media.offer(PACKET)
            assert status.error_code == "downstream_slow" and status.error_stage == "transport"
            status = MediaStatus()
            media = Media(settings, m.store, uuid4(), 1, status, lambda: None)
            media.offer(PACKET[:3] + b"\x90" + PACKET[4:])
            assert status.error_code == "cas_unavailable" and status.error_stage == "cas"
            status = MediaStatus()
            media = Media(
                replace(settings, max_hls_bytes=1), m.store, uuid4(), 1, status, lambda: None
            )
            media.directory.mkdir(parents=True)
            (media.directory / "segment_000000.ts").write_bytes(PACKET)
            media.publish()
            assert status.error_code == "hls_output_limit"
        finally:
            await m.close()

    asyncio.run(run())


def test_hls_snapshot_and_restart_revocation(settings: Settings) -> None:
    async def run() -> None:
        m = Manager(settings, adapter_factory=lambda _: Paced())
        session = await ready(m)
        status = MediaStatus()
        media = Media(settings, m.store, session.id, 1, status, lambda: None)
        media.directory.mkdir(parents=True)
        playlist = media.directory / "index.m3u8"
        (media.directory / "segment_000000.ts").write_bytes(PACKET)
        first = "#EXTM3U\n#EXTINF:2,\nsegment_000000.ts\n"
        playlist.write_text(first)
        media.publish()
        assert media.artifact
        assert m.store.artifact(str(media.artifact.id))[0].playlist_snapshot == first  # type: ignore[index]
        # FFmpeg has advanced before the next publication. Registered playlist
        # bytes stay coherent with registered members instead of reading disk.
        second = "#EXTM3U\n#EXTINF:2,\nsegment_000001.ts\n"
        playlist.write_text(second)
        media.publish()  # Segment publication race is retried, not a converter fault.
        assert status.state == "ready"
        (media.directory / "segment_000001.ts").write_bytes(PACKET)
        media.publish()
        assert m.store.artifact(str(media.artifact.id))[0].playlist_snapshot == second  # type: ignore[index]
        await m.close()
        m = Manager(settings)
        session.state, session.hls = State.running, status
        m.persist(session)
        await m.close()
        m = Manager(settings)
        recovered = m.sessions[session.id]
        assert recovered.state == State.interrupted and recovered.hls
        assert recovered.hls.state == "interrupted" and recovered.hls.url is None
        assert m.store.artifact(str(media.artifact.id))[0].partial  # type: ignore[index]
        await m.close()

    asyncio.run(run())


def test_recording_finishes_before_restore_and_lock_release(settings: Settings) -> None:
    async def run() -> None:
        class Ordered(Paced):
            async def stop(self) -> Restore:
                assert m.recordings.active is None and m.recordings.file is None
                with pytest.raises(DeviceBusy):
                    DeviceLock(m.device.root).acquire()
                return Restore.verified

        m = Manager(settings, adapter_factory=lambda _: Ordered())
        try:
            session = await ready(m)
            record = m.recordings.start(
                RecordingStart(request_id=uuid4(), session_id=session.id, duration_seconds=5)
            )
            m.stop(session.id)
            assert m.task
            await m.task
            assert record.partial and record.end_reason == EndReason.source_ended
            assert session.restore == Restore.verified
            probe = DeviceLock(m.device.root)
            probe.acquire()
            probe.release()
        finally:
            await m.close()

    asyncio.run(run())


def test_stop_timeout_blocks_restart(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sdr_dtv_poc.manager.STOP_GRACE", 0.02)

    async def run() -> None:
        class SlowStop(Paced):
            async def stop(self) -> Restore:
                await asyncio.sleep(60)
                return Restore.verified

        m = Manager(settings, adapter_factory=lambda _: SlowStop())
        try:
            session = await ready(m)
            m.stop(session.id)
            assert m.task
            await asyncio.wait_for(m.task, 1)
            assert session.partial and session.restore == Restore.unknown
            with pytest.raises(Conflict, match="restore_unverified"):
                m.start(SessionStart(request_id=uuid4()))
        finally:
            await m.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["partial", "missing", "codec", "deadline", "database"])
def test_playback_failure_does_not_change_recording(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def run() -> None:
        calls = 0

        def factory(source: Any) -> Paced:
            nonlocal calls
            calls += 1
            return Paced()

        m = Manager(settings, adapter_factory=factory)
        try:
            session = await ready(m)
            record = m.recordings.start(
                RecordingStart(request_id=uuid4(), session_id=session.id, duration_seconds=5)
            )
            await asyncio.sleep(0.01)
            m.recordings.finish(
                EndReason.worker_failed if failure == "partial" else EndReason.requested
            )
            if failure == "partial":
                with pytest.raises(Conflict, match="recording_incomplete"):
                    m.recordings.start_playback(record.id)
                return
            entry = m.store.artifact(str(record.artifact_id))
            assert entry
            original = settings.data_dir / entry[1]
            before = original.read_bytes()
            if failure == "missing":
                original.rename(original.with_suffix(".offline"))
                assert not m.recordings.get(record.id).file_available
                with pytest.raises(Conflict, match="recording_file_missing"):
                    m.recordings.start_playback(record.id)
                return
            if failure in {"deadline", "database"}:
                monkeypatch.setattr("sdr_dtv_poc.recording.PLAYBACK_LIMIT", 0.02)

                async def stalled(self: Media) -> None:
                    if failure == "database":
                        self.artifact = Artifact(
                            id=uuid4(),
                            session_id=self.owner,
                            kind="hls",
                            media_type="application/vnd.apple.mpegurl",
                            size_bytes=0,
                            sha256="",
                        )
                    await asyncio.sleep(10)

                monkeypatch.setattr(Media, "start", stalled)
            if failure == "database":

                def unavailable(artifact: Artifact) -> None:
                    raise sqlite3.OperationalError("simulated write failure")

                monkeypatch.setattr(m.store, "update_artifact", unavailable)
            playback = m.recordings.start_playback(record.id)
            assert m.recordings.start_playback(record.id) is playback
            assert m.recordings.playback_task
            await asyncio.wait_for(m.recordings.playback_task, 8)
            assert playback.state == "failed" and playback.url is None
            if failure == "deadline":
                assert playback.error_code == "playback_deadline"
            if failure == "database":
                assert playback.error_code == "database_error"
                assert playback.error_stage == "storage" and playback.ended_at
                assert m.storage_failed and m.recordings.media is None
                saved = Playback.model_validate_json(m.store.records("playbacks")[0][0])
                assert saved.ended_at == playback.ended_at and saved.error_code == "database_error"
            assert record.state == State.completed and not record.partial
            assert original.read_bytes() == before and calls == 1
        finally:
            await m.close()

    asyncio.run(run())
