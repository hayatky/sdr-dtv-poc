# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from sdr_dtv_poc.config import Settings
from sdr_dtv_poc.device_lock import DeviceBusy, DeviceLock
from sdr_dtv_poc.live_config import LiveConfig
from sdr_dtv_poc.manager import Conflict, Manager
from sdr_dtv_poc.models import InputKind, Restore, Scan, ScanStart, Session, SessionStart, State
from sdr_dtv_poc.recovery_worker import restore, run

BASELINE = {
    "sampling_frequency": "2500000",
    "rf_bandwidth": "1500000",
    "frequency": "2400000000",
    "gain_control_mode": "slow_attack",
    "hardwaregain": "57 dB",
}
CONFIGURED = {
    "sampling_frequency": "6400000",
    "rf_bandwidth": "6000000",
    "frequency": "503142857",
    "gain_control_mode": "manual",
    "hardwaregain": "20 dB",
}


def fake_worker(monkeypatch: pytest.MonkeyPatch, current: dict[str, str]) -> SimpleNamespace:
    from decimal import Decimal

    values = current.copy()
    writes: list[tuple[str, str]] = []
    connections: list[Mock] = []

    def connect(*args: object, **kwargs: object) -> Mock:
        sock = Mock()
        connections.append(sock)
        return sock

    class Session:
        def __init__(self, *args: object):
            self.stream = Mock()

        def read(self, d: str, c: str, attr: str) -> str:
            return values[attr]

        def write(self, d: str, c: str, attr: str, value: str) -> None:
            assert attr in BASELINE
            writes.append((attr, value))
            values[attr] = value

    def matches(name: str, actual: str, expected: str) -> bool:
        return (
            actual == expected
            if name == "gain_control_mode"
            else Decimal(actual.split()[0]) == Decimal(expected.split()[0])
        )

    rx = SimpleNamespace(
        SETTINGS=[("INPUT", "voltage0", k, v) for k, v in BASELINE.items()],
        HOST="board.invalid",
        check_route=Mock(),
        Session=Session,
        validate_context=lambda xml: ("phy", "rx", []),
        utc=lambda: "2026-10-04T00:00:00Z",
        probe=SimpleNamespace(fetch_payload=lambda *args: (1, b"x"), MAX_XML_BYTES=1024),
    )
    monkeypatch.setattr("sdr_dtv_poc.recovery_worker.socket.create_connection", connect)
    return SimpleNamespace(rx=rx, matches=matches, writes=writes, connections=connections)


def test_recovery_restores_then_independently_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = fake_worker(monkeypatch, CONFIGURED)
    report: dict[str, object] = {"commands": []}
    restore(worker, {"baseline": BASELINE, "readback": CONFIGURED}, report)
    assert len(worker.connections) == 2
    assert len(worker.writes) == 4  # AGC gain is intentionally not written
    assert DeviceLock.baseline_matches(BASELINE, report["after"])
    for connection in worker.connections:
        connection.close.assert_called_once()


def test_recovery_already_restored_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = fake_worker(monkeypatch, BASELINE)
    restore(worker, {"baseline": BASELINE, "readback": CONFIGURED}, {"commands": []})
    assert not worker.writes and len(worker.connections) == 2


def test_unexpected_settings_and_missing_baseline_refuse_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = fake_worker(monkeypatch, {**CONFIGURED, "frequency": "123000000"})
    with pytest.raises(ValueError, match="settings_changed"):
        restore(worker, {"baseline": BASELINE, "readback": CONFIGURED}, {"commands": []})
    assert not worker.writes
    with pytest.raises(ValueError, match="recovery_evidence_missing"):
        restore(worker, {"baseline": {}, "readback": CONFIGURED}, {"commands": []})
    assert not worker.writes


@pytest.mark.parametrize("verified_job", [False, True])
def test_worker_keeps_failed_job_and_retries_after_proof_before_marker_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified_job: bool
) -> None:

    root = tmp_path / "device"
    lock = DeviceLock(root)
    lock.acquire(recovery=True)
    folder = root / ("poc-" + str(uuid4()))
    folder.mkdir()
    job: dict[str, object] = {
        "id": folder.name,
        "owner": "sdr-dtv-poc",
        "state": "failed",
        "baseline": BASELINE,
        "readback": CONFIGURED,
        "restoration": {"state": "unknown"},
    }
    if verified_job:
        job.update(restoration={"state": "restored"}, independent_readback=BASELINE)
    raw = json.dumps(job)
    (folder / "job.json").write_text(raw)
    marker = root / "recovery-required.json"
    marker_raw = json.dumps({"job_id": folder.name})
    marker.write_text(marker_raw)
    worker = fake_worker(monkeypatch, CONFIGURED)
    monkeypatch.setattr(
        "sdr_dtv_poc.recovery_worker.LiveConfig.read",
        lambda path: SimpleNamespace(research_root=tmp_path),
    )
    original_import = importlib.import_module
    monkeypatch.setattr(
        "sdr_dtv_poc.recovery_worker.importlib.import_module",
        lambda name: worker if name == "receiver.worker" else original_import(name),
    )
    monkeypatch.setattr("sdr_dtv_poc.recovery_worker.signal.alarm", lambda seconds: None)
    try:
        assert lock.fd is not None
        run(tmp_path / "config", root, tmp_path / "result", lock.fd)
        assert not marker.exists() and DeviceLock.recovered(folder, job)
        assert (folder / "job.json").read_text() == raw
        # Simulate a crash after writing the verified proof but before archiving marker.
        marker.write_text(marker_raw)
        run(tmp_path / "config", root, tmp_path / "result2", lock.fd)
        assert not marker.exists()
        assert len(list(folder.glob("recovery-attempt-*.json"))) == 2
    finally:
        lock.release()
    lock.acquire()
    lock.release()


def test_crash_after_restore_requires_independent_readback(tmp_path: Path) -> None:
    folder = tmp_path / "poc-job"
    folder.mkdir()
    job: dict[str, object] = {
        "owner": "sdr-dtv-poc",
        "id": "poc-job",
        "state": "completed",
        "baseline": BASELINE,
        "restoration": {"state": "restored"},
    }
    path = folder / "job.json"
    path.write_text(json.dumps(job))
    assert DeviceLock(tmp_path).pending_jobs()
    assert not DeviceLock.verified(folder, job)
    job["independent_readback"] = BASELINE
    path.write_text(json.dumps(job))
    assert not DeviceLock(tmp_path).pending_jobs()
    assert DeviceLock.verified(folder, job)


def test_db_only_uncertainty_recovers_without_rewriting_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        m = Manager(
            Settings(
                data_dir=tmp_path / "app",
                device_lock_dir=tmp_path / "device",
                live=LiveConfig(tmp_path, Path("/usr/bin/python3"), {}, tmp_path / "config"),
            )
        )
        request = SessionStart(request_id=uuid4(), input_kind=InputKind.live)
        session = Session(
            id=uuid4(),
            request_id=request.request_id,
            input_kind=InputKind.live,
            source_id="uhf-18",
            state=State.failed,
            restore=Restore.unknown,
            partial=True,
            duration_seconds=10,
            started_at="2026-10-04T00:00:00Z",
            deadline_at="2026-10-04T00:00:10Z",
        )
        m.sessions[session.id] = session
        m.requests[request.request_id] = (session.id, request.model_dump_json())
        m.persist(session)
        folder = m.device.root / f"poc-{session.id}"
        folder.mkdir(parents=True)
        job: dict[str, object] = {
            "owner": "sdr-dtv-poc",
            "id": folder.name,
            "state": "failed",
            "baseline": BASELINE,
            "restoration": {"state": "restored"},
            "independent_readback": BASELINE,
        }
        (folder / "job.json").write_text(json.dumps(job))
        try:
            first = m.recovery.start()
            assert first["state"] == "running"
            assert m.recovery.start()["state"] == "running"
            with pytest.raises(Conflict, match="device_busy"):
                m.check_idle()
            with pytest.raises(DeviceBusy, match="device_busy"):
                DeviceLock(m.device.root).acquire(recovery=True)
            assert m.recovery.task
            await m.recovery.task
            assert m.recovery.state == "completed"
            assert (
                session.restore == Restore.verified
                and session.state == State.failed
                and session.partial
            )
            assert (folder / "job.json").read_text() == json.dumps(job)
        finally:
            await m.close()

    asyncio.run(scenario())


def test_recovery_api_requires_csrf_and_read_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from sdr_dtv_poc.app import create_app
    from sdr_dtv_poc.recovery import Recovery

    start = Mock(return_value={"state": "running", "error_code": None, "required": True})
    monkeypatch.setattr(Recovery, "start", start)
    origin = "http://localhost:8000"
    with TestClient(create_app(Settings(data_dir=tmp_path)), base_url=origin) as client:
        assert client.get("/api/recovery").json()["state"] == "idle"
        assert client.post("/api/recovery", json={}).status_code == 403
        start.assert_not_called()
        token = client.get("/api/bootstrap").json()["csrf_token"]
        assert (
            client.post(
                "/api/recovery", json={}, headers={"Origin": origin, "X-CSRF-Token": token}
            ).status_code
            == 202
        )
        start.assert_called_once()


def test_interrupted_scan_and_shared_jobs_expose_recovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        settings = Settings(data_dir=tmp_path / "app", device_lock_dir=tmp_path / "device")
        m = Manager(settings)
        request = ScanStart(request_id=uuid4(), input_kind=InputKind.live)
        scan = Scan(
            id=uuid4(),
            request_id=request.request_id,
            input_kind=InputKind.live,
            state=State.running,
            restore=Restore.pending,
            total_channels=1,
            results=[],
            started_at="2026-10-04T00:00:00Z",
            deadline_at="2026-10-04T00:20:00Z",
        )
        m.store.save_record("scans", scan, request)
        await m.close()
        m = Manager(settings)
        try:
            assert m.scans.items[scan.id].state == State.interrupted
            assert m.recovery.status()["required"] is True
            with pytest.raises(Conflict, match="restore_unverified"):
                m.check_idle()
            m.scans.items.clear()
            folder = m.device.root / "poc-abandoned"
            folder.mkdir(parents=True)
            path = folder / "job.json"
            path.write_text(json.dumps({"state": "running", "restoration": {"state": "pending"}}))
            assert m.recovery.status()["required"] is True
            # The same pending job is normal while a local scan is running.
            m.scans.task = asyncio.create_task(asyncio.sleep(0))
            assert m.recovery.status()["required"] is False
            await m.scans.task
            path.write_text("{")
            assert m.recovery.status()["required"] is True
            assert m.recovery.task is None  # GET never starts device work.
        finally:
            await m.close()

    asyncio.run(scenario())


def test_recovery_timeout_reaps_child_and_keeps_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    async def scenario() -> None:
        m = Manager(
            Settings(
                data_dir=tmp_path / "app",
                device_lock_dir=tmp_path / "device",
                live=LiveConfig(tmp_path, Path("/usr/bin/python3"), {}, tmp_path / "config"),
            )
        )
        folder = m.device.root / "poc-failed"
        folder.mkdir(parents=True)
        (folder / "job.json").write_text(
            json.dumps({"id": "poc-failed", "state": "failed", "restoration": {"state": "unknown"}})
        )
        m.device.block(uuid4())
        finished = asyncio.Event()
        child = Mock(returncode=None)

        async def wait() -> int:
            await finished.wait()
            return -9

        def kill() -> None:
            child.returncode = -9
            finished.set()

        child.wait = wait
        child.kill = kill

        async def spawn(*args: object, **kwargs: object) -> Mock:
            return child

        original_timeout = asyncio.timeout
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(asyncio, "timeout", lambda seconds: original_timeout(0.01))
        try:
            m.recovery.start()
            assert m.recovery.task
            await m.recovery.task
            assert m.recovery.state == "failed" and m.recovery.required()
            assert child.returncode == -9 and m.device.fd is None
            other = DeviceLock(m.device.root)
            other.acquire(recovery=True)
            other.release()
        finally:
            await m.close()

    asyncio.run(scenario())
