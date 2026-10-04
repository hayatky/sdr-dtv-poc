# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest

from sdr_dtv_poc.device_lock import DeviceBusy, DeviceLock
from sdr_dtv_poc.live_config import LiveConfig, LiveProfile


def test_research_pending_and_late_recovery_share_gate(tmp_path: Path) -> None:
    folder = tmp_path / "capture"
    folder.mkdir()
    baseline = {
        "frequency": "2400000000",
        "gain_control_mode": "slow_attack",
        "hardwaregain": "57 dB",
    }
    job = {
        "id": "capture",
        "state": "failed",
        "baseline": baseline,
        "restoration": {"state": "unknown"},
    }
    (folder / "job.json").write_text(json.dumps(job))
    lock = DeviceLock(tmp_path)
    with pytest.raises(DeviceBusy, match="restore_unverified"):
        lock.acquire()
    # A marker's deletion cannot bypass the unresolved research job.
    recovery = {
        "capture_id": "capture",
        "state": "restored_readback_verified",
        "baseline": baseline,
        "after": {**baseline, "frequency": "2410000000"},
    }
    (folder / "recovery.json").write_text(json.dumps(recovery))
    with pytest.raises(DeviceBusy, match="restore_unverified"):
        lock.acquire()
    recovery["after"] = {**baseline, "hardwaregain": "61 dB"}
    (folder / "recovery.json").write_text(json.dumps(recovery))
    lock.acquire()
    lock.release()
    job["state"] = "capturing"
    (folder / "job.json").write_text(json.dumps(job))
    with pytest.raises(DeviceBusy, match="restore_unverified"):
        lock.acquire()


def test_live_profiles_require_explicit_measured_conditions(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        LiveProfile(channel=21)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        LiveProfile(
            channel=21, mode=3, gi=0.125, rate_b=2, interleave_a=4, interleave_b=2, gain_db=71
        )
    path = tmp_path / "live.json"
    path.write_text(
        json.dumps(
            {
                "research_root": "/research",
                "native_python": "/usr/bin/python3",
                "profiles": {
                    "ch21": {
                        "channel": 21,
                        "mode": 3,
                        "gi": 0.125,
                        "rate_b": 2,
                        "interleave_a": 4,
                        "interleave_b": 2,
                    }
                },
                "command": "unexpected",
            }
        )
    )
    with pytest.raises(ValueError, match="unexpected"):
        LiveConfig.read(path)


def test_scan_cancellation_waits_for_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from uuid import uuid4

    from sdr_dtv_poc.config import Settings
    from sdr_dtv_poc.manager import Manager
    from sdr_dtv_poc.models import InputKind, Restore, ScanStart

    async def scenario() -> None:
        stopping, release = asyncio.Event(), asyncio.Event()

        class FakeLive:
            failed = False

            def __init__(self, *args: object) -> None:
                pass

            async def start(self) -> None:
                (tmp_path / "app/native").mkdir(parents=True, exist_ok=True)
                # live_probe owns the scan output path.
                for scan in manager.scans.items.values():
                    (tmp_path / f"app/native/{scan.id}-21").mkdir(exist_ok=True)

            async def read(self) -> bytes:
                return b""

            async def stop(self) -> Restore:
                stopping.set()
                await release.wait()
                return Restore.verified

        monkeypatch.setattr("sdr_dtv_poc.scanning.LiveAdapter", FakeLive)
        live = LiveConfig(
            Path("/research"),
            Path("/usr/bin/python3"),
            {"ch21": LiveProfile(21, 3, 0.125, 2, 4, 2)},
            tmp_path / "config.json",
        )
        settings = Settings(
            data_dir=tmp_path / "app", live=live, device_lock_dir=tmp_path / "device"
        )
        manager = Manager(settings)
        try:
            scan = manager.scans.start(
                ScanStart(request_id=uuid4(), input_kind=InputKind.live, channels=[21])
            )
            await stopping.wait()
            manager.scans.stop(scan.id)
            await asyncio.sleep(0.02)
            assert manager.scans.task and not manager.scans.task.done()
            with pytest.raises(DeviceBusy, match="device_busy"):
                DeviceLock(tmp_path / "device").acquire()
            release.set()
            await manager.scans.task
            assert scan.restore == Restore.verified
            lock = DeviceLock(tmp_path / "device")
            lock.acquire()
            lock.release()
        finally:
            release.set()
            await manager.close()

    asyncio.run(scenario())


def test_cas_flush_failure_is_not_completed(tmp_path: Path) -> None:
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from sdr_dtv_poc.config import Settings
    from sdr_dtv_poc.media import Media
    from sdr_dtv_poc.models import MediaStatus
    from sdr_dtv_poc.store import Store

    async def scenario() -> None:
        store = Store(tmp_path / "state.sqlite3")
        status = MediaStatus(state="ready", ready_at="2026-10-04T00:00:00Z")
        media = Media(Settings(data_dir=tmp_path), store, uuid4(), 1, status, lambda: None)
        media.process = Mock(returncode=0, wait=AsyncMock(return_value=0))
        media.cas = Mock(returncode=1, wait=AsyncMock(return_value=1))
        media.publish = Mock()  # type: ignore[method-assign]
        try:
            await media.close()
            assert status.state == "failed"
            assert status.error_stage == "cas"
            assert status.error_code == "cas_failed"
        finally:
            store.close()

    asyncio.run(scenario())
