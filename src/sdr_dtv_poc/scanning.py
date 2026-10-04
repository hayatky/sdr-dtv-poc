# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite file/profile scan; saved TS never implies current RF reception."""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .adapter import LiveAdapter, LiveWorkerFailed
from .config import Source
from .live_config import LiveProfile
from .media import timestamp
from .models import EndReason, InputKind, Restore, Scan, ScanResult, ScanStart, Service, State

if TYPE_CHECKING:
    from .manager import Manager


def frequency(channel: int) -> int:
    if not 13 <= channel <= 52:
        raise ValueError("invalid channel")
    return 473_142_857 + (channel - 13) * 6_000_000


def transport_ids(path: Path) -> tuple[int | None, int | None]:
    """Read the ID fields of single-packet PAT/SDT sections; otherwise unknown.

    Service names and program membership come from FFmpeg. No ARIB text decoder
    or demodulation is implemented here.
    """
    tsid = onid = None
    with path.open("rb") as file:
        data = file.read(188 * 6000)
    for offset in range(0, len(data) - 187, 188):
        packet = data[offset : offset + 188]
        if packet[0] != 0x47 or not packet[1] & 0x40:
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        control = (packet[3] >> 4) & 3
        if pid not in (0, 17) or control not in (1, 3):
            continue
        start = 4 if control == 1 else 5 + packet[4]
        if start >= 187:
            continue
        start += 1 + packet[start]
        section = packet[start:]
        if len(section) < 11 or not section[5] & 1:
            continue
        if pid == 0 and section[0] == 0:
            tsid = int.from_bytes(section[3:5])
        elif pid == 17 and section[0] == 0x42:
            onid = int.from_bytes(section[8:10])
    return tsid, onid


async def probe(source: Source, channel: int, source_id: str, kind: InputKind) -> list[Service]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sdr_dtv_poc.child_exec",
        str(os.getpid()),
        "ffprobe",
        "-v",
        "error",
        "-probesize",
        "1000000",
        "-analyzeduration",
        "1000000",
        "-show_programs",
        "-of",
        "json",
        str(source.path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            assert process.stdout
            try:
                result = await process.stdout.readexactly(1_048_577)
            except asyncio.IncompleteReadError as exc:
                result = exc.partial
            if len(result) > 1_048_576 or await process.wait() != 0:
                return []
        parsed = json.loads(result)
        tsid, onid = transport_ids(source.path)
        services = []
        for program in parsed.get("programs", []):
            if kind == InputKind.live and not {"video", "audio"}.issubset(
                {s.get("codec_type") for s in program.get("streams", [])}
            ):
                continue
            sid = int(program["program_id"])
            key = str(
                uuid5(NAMESPACE_URL, f"sdr-dtv:{kind}:{channel}:{onid}:{tsid}:{sid}:{source_id}")
            )
            services.append(
                Service(
                    id=key,
                    name=program.get("tags", {}).get("service_name"),
                    input_kind=kind,
                    physical_channel=channel,
                    frequency_hz=frequency(channel),
                    service_id=sid,
                    original_network_id=onid,
                    transport_stream_id=tsid,
                    detection_stage="ts_si",
                    source_id=source_id,
                    detected_at=timestamp(),
                    profile={"bitrate": source.bitrate, "tmcc": "not_checked"},
                )
            )
        return services
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


class Scans:
    def __init__(self, manager: "Manager"):
        self.manager = manager
        self.items: dict[UUID, Scan] = {}
        self.requests: dict[UUID, tuple[UUID, str]] = {}
        self.services: dict[str, Service] = {}
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self.stop_reason = EndReason.requested
        for body, _ in manager.store.records("services"):
            service = Service.model_validate_json(body)
            self.services[service.id] = service
        for body, request in manager.store.records("scans"):
            scan = Scan.model_validate_json(body)
            if scan.state in {State.starting, State.running, State.stopping}:
                scan.state, scan.end_reason = State.interrupted, EndReason.server_restart
                scan.ended_at = timestamp()
                manager.store.save_record("scans", scan)
            self.items[scan.id] = scan
            self.requests[scan.request_id] = (scan.id, request)

    def start(self, request: ScanStart) -> Scan:
        from .manager import Conflict, Unavailable

        previous = self.requests.get(request.request_id)
        if previous:
            if previous[1] != request.model_dump_json():
                raise Conflict("request_id_reused")
            return self.items[previous[0]]
        m = self.manager
        m.check_idle()
        if request.input_kind == InputKind.live and (
            not m.settings.live or not m.settings.device_lock_dir
        ):
            raise Unavailable("live_not_configured")
        if request.input_kind == InputKind.saved_ts:
            raise Unavailable("saved_scan_not_configured")
        if len(self.items) >= 1000:
            raise Conflict("scan_history_limit")
        scan = Scan(
            id=uuid4(),
            request_id=request.request_id,
            input_kind=request.input_kind,
            total_channels=len(request.channels),
            started_at=timestamp(),
            deadline_at=(
                datetime.now(UTC) + timedelta(seconds=request.duration_seconds)
            ).isoformat(),
            results=[
                ScanResult(physical_channel=ch, frequency_hz=frequency(ch))
                for ch in request.channels
            ],
        )
        m.acquire_device()
        try:
            m.store.save_record("scans", scan, request)
        except sqlite3.Error:
            m.device.release()
            m.storage_failed = True
            raise Unavailable("database_error") from None
        self.items[scan.id] = scan
        self.requests[request.request_id] = (scan.id, request.model_dump_json())
        self.stop_event, self.stop_reason = asyncio.Event(), EndReason.requested
        self.task = asyncio.create_task(self.run(scan, request.duration_seconds))
        return scan

    def stop(self, scan_id: UUID) -> Scan:
        scan = self.items[scan_id]
        if scan.state in {State.starting, State.running, State.stopping}:
            scan.state = State.stopping
            self.stop_event.set()
        return scan

    async def discover(self, scan: Scan, channel: int) -> LiveProfile | None:
        m = self.manager
        assert m.settings.live and m.device.fd is not None
        directory = m.settings.data_dir / "native" / f"{scan.id}-{channel}-discovery"
        adapter = LiveAdapter(
            m.settings,
            Source(m.settings.live.config_path, 0, live_id=str(channel)),
            directory,
            45,
            m.device.fd,
        )
        adapter.worker_module = "sdr_dtv_poc.discovery_worker"
        scan.restore = Restore.pending
        row = next(r for r in scan.results if r.physical_channel == channel)
        row.state = "searching_tmcc"
        m.store.save_record("scans", scan)
        try:
            await adapter.start()
            assert adapter.process
            await asyncio.wait_for(adapter.process.wait(), 50)
        finally:
            cleanup = asyncio.create_task(adapter.stop())
            interrupted = False
            while True:
                try:
                    scan.restore = await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    interrupted = True
            if scan.restore not in {Restore.verified, Restore.not_required}:
                m.device.block(scan.id)
            m.store.save_record("scans", scan)
            if interrupted:
                raise asyncio.CancelledError
        if scan.restore != Restore.verified or adapter.failed:
            row.state, row.error_code = "failed", "discovery_failed"
            raise RuntimeError("channel discovery failed")
        data = json.loads((directory / "discovery.json").read_text())
        row.state = data["state"]
        if row.state != "tmcc_detected":
            return None
        return LiveProfile(**data["profile"])

    async def live_probe(self, scan: Scan, channel: int, remaining: float) -> list[Service]:
        m = self.manager
        assert m.settings.live and m.device.fd is not None
        began_discovery = m.clock()
        profile = await self.discover(scan, channel)
        if profile is None:
            return []
        remaining -= m.clock() - began_discovery
        if remaining < 5:
            await asyncio.sleep(max(0, remaining) + 1)
        source_id = next(
            (k for k, p in m.settings.live.profiles.items() if p.channel == channel),
            f"uhf-{channel}",
        )
        directory = m.settings.data_dir / "native" / f"{scan.id}-{channel}"
        adapter = LiveAdapter(
            m.settings,
            m.live_source(source_id, profile),
            directory,
            max(1, min(18, int(remaining))),
            m.device.fd,
        )
        scan.restore = Restore.pending
        m.store.save_record("scans", scan)
        began = m.clock()
        count = 0
        try:
            await adapter.start()
            with (directory / "scan.ts").open("xb") as output:
                while m.clock() - began < min(14, remaining):
                    async with asyncio.timeout(max(0.01, min(14, remaining) - (m.clock() - began))):
                        data = await adapter.read()
                    if not data:
                        break
                    count += len(data)
                    if count > 24 * 1024 * 1024:
                        break
                    output.write(data)
        except TimeoutError:
            pass
        except LiveWorkerFailed:
            # Classify after stop has collected the worker's restoration and
            # pipeline result. Never continue after a capture/I/O failure.
            pass
        finally:
            cleanup = asyncio.create_task(adapter.stop())
            interrupted = False
            while True:
                try:
                    scan.restore = await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    interrupted = True
            if scan.restore not in {Restore.verified, Restore.not_required}:
                m.device.block(scan.id)
            m.store.save_record("scans", scan)
            if interrupted:
                raise asyncio.CancelledError
        if scan.restore != Restore.verified:
            raise RuntimeError("live scan failed")
        if adapter.failed:
            report = json.loads((directory / "live-result.json").read_text())
            errors = set(report.get("errors", []))
            if errors and errors <= {
                "tmcc_profile_mismatch",
                "native_result_missing",
                "no_ts_sync",
                "invalid_initial_ts",
                "native_process_failed",
                "native_process_ended",
            }:
                row = next(r for r in scan.results if r.physical_channel == channel)
                row.state, row.error_code = "no_service", "demodulation_failed"
                return []
            raise RuntimeError("live scan worker failed")
        found = await probe(
            Source(directory / "scan.ts", 18_000_000), channel, source_id, InputKind.live
        )
        for service in found:
            service.profile = {
                **asdict(profile),
                "sample_rate_hz": 6_400_000,
                "tmcc": "detected_and_verified",
                "layer": profile.layer,
            }
        return found

    async def run(self, scan: Scan, duration: int) -> None:
        m = self.manager
        began = m.clock()
        stop = asyncio.create_task(self.stop_event.wait())
        task: asyncio.Task[list[Service]] | None = None
        try:
            scan.state = State.running
            for result in scan.results:
                if self.stop_event.is_set():
                    scan.end_reason = self.stop_reason
                    break
                if (
                    shutil.disk_usage(m.settings.data_dir).free
                    < m.settings.min_free_bytes + 128 * 1024 * 1024
                ):
                    raise OSError("insufficient scan space")
                remaining = duration - (m.clock() - began)
                if remaining <= 0:
                    scan.end_reason = EndReason.deadline
                    break
                scan.current_channel = result.physical_channel
                source_id = (
                    "demo" if result.physical_channel == 13 else f"demo-{result.physical_channel}"
                )
                source = m.synthetic_source(source_id)
                found: list[Service] = []
                if scan.input_kind == InputKind.live or (source and source.path.is_file()):
                    if scan.input_kind == InputKind.live:
                        task = asyncio.create_task(
                            self.live_probe(scan, result.physical_channel, remaining)
                        )
                    else:
                        assert source
                        task = asyncio.create_task(
                            probe(source, result.physical_channel, source_id, scan.input_kind)
                        )
                    done, _ = await asyncio.wait(
                        [task, stop], timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                    )
                    if stop in done or task not in done:
                        scan.end_reason = self.stop_reason if stop in done else EndReason.deadline
                        break
                    found = task.result()
                result.state = (
                    "detected"
                    if found
                    else "no_service"
                    if result.state == "tmcc_detected"
                    else "not_detected"
                    if result.state == "not_run"
                    else result.state
                )
                for service in found:
                    m.store.save_record("services", service)
                    self.services[service.id] = service
                    result.service_ids.append(service.id)
                scan.completed_channels += 1
                scan.elapsed_seconds = m.clock() - began
                m.store.save_record("scans", scan)
                await asyncio.sleep(0)
            scan.state = State.completed
            scan.end_reason = scan.end_reason or EndReason.eof
        except sqlite3.Error:
            m.storage_failed = True
            scan.state, scan.end_reason = State.failed, EndReason.database_error
        except Exception:
            scan.state, scan.end_reason = State.failed, EndReason.worker_failed
        finally:
            for pending in (task, stop):
                if pending:
                    pending.cancel()
            cleanup_results = await asyncio.gather(
                *(t for t in (task, stop) if t), return_exceptions=True
            )
            cleanup_failed = any(
                isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)
                for r in cleanup_results
            )
            if scan.input_kind == InputKind.live and (
                scan.restore not in {Restore.verified, Restore.not_required} or cleanup_failed
            ):
                scan.state, scan.end_reason = State.failed, EndReason.worker_failed
                if scan.restore not in {Restore.verified, Restore.not_required}:
                    m.device.block(scan.id)
            scan.ended_at, scan.current_channel = timestamp(), None
            scan.elapsed_seconds = m.clock() - began
            try:
                m.store.save_record("scans", scan)
            except sqlite3.Error:
                m.storage_failed = True
                scan.state, scan.end_reason = State.failed, EndReason.database_error
            m.device.release()

    async def close(self) -> None:
        if self.task and not self.task.done():
            self.stop_reason = EndReason.server_shutdown
            self.stop_event.set()
            await self.task
