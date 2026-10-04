# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json
import signal
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from sdr_dtv_poc.adapter import LiveWorkerFailed
from sdr_dtv_poc.config import Settings
from sdr_dtv_poc.discovery_worker import profile_from_tmcc
from sdr_dtv_poc.live_config import LiveConfig, LiveProfile
from sdr_dtv_poc.live_worker import force_stop_group
from sdr_dtv_poc.manager import Manager
from sdr_dtv_poc.models import InputKind, Restore, Scan, ScanStart, Service, SessionStart, State


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        device_lock_dir=tmp_path / "device",
        live=LiveConfig(tmp_path / "source", Path("/usr/bin/python3"), {}, tmp_path / "live.json"),
    )


def test_tmcc_selects_measured_profile_and_rejects_unsupported_layers() -> None:
    frame = {
        "parity_valid": True,
        "current_layers": {
            "A": {"modulation": "QPSK", "code_rate": "2/3", "interleave_length": 8, "segments": 1},
            "B": {
                "modulation": "64QAM",
                "code_rate": "5/6",
                "interleave_length": 4,
                "segments": 12,
            },
            "C": {"modulation": "unused", "code_rate": "unused", "segments_code": 15},
        },
    }
    p = profile_from_tmcc(35, 2, 0.25, frame)
    assert (p.channel, p.mode, p.gi, p.rate_b, p.interleave_b) == (35, 2, 0.25, 3, 4)
    frame["parity_valid"] = False
    with pytest.raises(ValueError):
        profile_from_tmcc(35, 2, 0.25, frame)


def test_recovery_marker_failure_still_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    killed = []
    monkeypatch.setattr("sdr_dtv_poc.live_worker.os.killpg", lambda group, sig: killed.append(sig))

    def full() -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        force_stop_group(full)
    assert killed == [signal.SIGKILL]


def test_a_only_profile_preserves_measured_modulation_and_partial_reception() -> None:
    unused = {"modulation": "unused", "code_rate": "unused", "segments_code": 15}
    frame = {
        "parity_valid": True,
        "partial_reception": False,
        "current_layers": {
            "A": {
                "modulation": "64QAM",
                "code_rate": "7/8",
                "interleave_length": 2,
                "segments": 13,
            },
            "B": unused,
            "C": unused,
        },
    }
    profile = profile_from_tmcc(13, 3, 0.125, frame)
    service = Service(
        id="a-only",
        name=None,
        input_kind=InputKind.live,
        physical_channel=13,
        frequency_hz=473_142_857,
        service_id=1,
        detection_stage="ts_si",
        source_id="uhf-13",
        detected_at="2026-01-01T00:00:00+00:00",
        profile=asdict(profile),
    )
    restored = LiveProfile.from_service(
        Service.model_validate_json(service.model_dump_json()).profile
    )
    assert restored == profile
    assert (restored.layer, restored.modulation_a, restored.rate_a) == ("a", 64, 4)
    assert restored.partial_reception is False
    assert (restored.segments_a, restored.segments_b) == (13, 0)


def test_restored_demodulation_failure_does_not_abort_later_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Adapter:
        failed = True

        def __init__(self, config: Any, source: Any, directory: Path, *args: Any):
            self.directory = directory

        async def start(self) -> None:
            self.directory.mkdir(parents=True)
            (self.directory / "live-result.json").write_text(
                json.dumps({"restore": "verified", "errors": ["tmcc_profile_mismatch"]})
            )

        async def read(self) -> bytes:
            raise LiveWorkerFailed("no stable TS")

        async def stop(self) -> Restore:
            return Restore.verified

    async def scenario() -> None:
        m = Manager(settings(tmp_path))
        seen = []

        async def discover(scan: Scan, channel: int) -> LiveProfile:
            seen.append(channel)
            scan.restore = Restore.verified
            return LiveProfile(channel, 3, 0.125, 2, 4, 2)

        monkeypatch.setattr(m.scans, "discover", discover)
        monkeypatch.setattr("sdr_dtv_poc.scanning.LiveAdapter", Adapter)
        scan = m.scans.start(
            ScanStart(request_id=uuid4(), input_kind=InputKind.live, channels=[13, 52])
        )
        assert m.scans.task
        await m.scans.task
        assert seen == [13, 52] and scan.state == State.completed
        assert all(
            r.state == "no_service" and r.error_code == "demodulation_failed" for r in scan.results
        )
        assert scan.restore == Restore.verified
        await m.close()

    asyncio.run(scenario())


def test_cancelled_scan_with_unknown_restore_is_failed_and_blocks_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        m = Manager(settings(tmp_path))
        entered = asyncio.Event()

        async def probe(scan: Scan, channel: int, remaining: float) -> list[Service]:
            scan.restore = Restore.pending
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                scan.restore = Restore.unknown
            return []

        monkeypatch.setattr(m.scans, "live_probe", probe)
        scan = m.scans.start(
            ScanStart(request_id=uuid4(), input_kind=InputKind.live, channels=[13, 52])
        )
        await entered.wait()
        m.scans.stop(scan.id)
        assert m.scans.task
        await m.scans.task
        assert scan.state == State.failed and scan.restore == Restore.unknown
        assert (tmp_path / "device/recovery-required.json").is_file()
        await m.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("gain", [None, 30])
def test_discovered_profile_survives_restart_and_is_used_for_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gain: int | None
) -> None:
    used = []

    class Adapter:
        failed = False

        def __init__(self, config: Any, source: Any, *args: Any):
            used.append(source.live_profile)

        async def start(self) -> None:
            pass

        async def read(self) -> bytes:
            return b""

        async def stop(self) -> Restore:
            return Restore.verified

        def request_stop(self) -> None:
            pass

        def metrics(self) -> dict[str, int]:
            return {}

    async def scenario() -> None:
        config = settings(tmp_path)
        m = Manager(config)
        profile = LiveProfile(35, 2, 0.25, 3, 8, 4)
        service = Service(
            id="discovered",
            name=None,
            input_kind=InputKind.live,
            physical_channel=35,
            service_id=123,
            source_id="uhf-35",
            detection_stage="ts_si",
            detected_at="2026-10-04T00:00:00Z",
            profile=asdict(profile),
        )
        m.store.save_record("services", service)
        await m.close()
        if gain is not None:
            assert config.live
            # The preset name and demodulation parameters differ from the
            # saved station. Only its physical channel's RX gain may override.
            config.live.profiles["renamed"] = LiveProfile(35, 3, 0.125, 2, 4, 2, gain_db=gain)
        m = Manager(config)
        monkeypatch.setattr("sdr_dtv_poc.manager.LiveAdapter", Adapter)
        session = m.start(
            SessionStart(
                request_id=uuid4(), service_key="discovered", duration_seconds=10, enable_hls=False
            )
        )
        assert session.service and session.service.physical_channel == 35
        assert used == [replace(profile, gain_db=gain if gain is not None else 20)]
        assert session.service.profile == asdict(profile)
        assert m.task
        await m.task
        await m.close()

    asyncio.run(scenario())
