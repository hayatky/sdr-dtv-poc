# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite RX supervisor using separately acquired research capture/demodulation.

No Docker, sudo, CAS implementation, or independent demodulator. stdout is TS.
Private capture/readback evidence stays in the shared device directory.
"""

import argparse
import errno
import importlib
import json
import os
import queue
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .live_config import LiveConfig, LiveProfile


def save(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False) + "\n")
    temp.replace(path)


def force_stop_group(mark_recovery: Callable[[], None]) -> None:
    try:
        mark_recovery()
    finally:
        os.killpg(os.getpgrp(), signal.SIGKILL)


def verify_restoration(worker: Any, job: dict[str, Any]) -> bool:
    if job["restoration"]["state"] == "restored":
        with socket.create_connection((worker.rx.HOST, 30431), timeout=2) as sock:
            sock.settimeout(2)
            stream = sock.makefile("rb")
            try:
                _, xml = worker.rx.probe.fetch_payload(
                    sock, stream, "PRINT", worker.rx.probe.MAX_XML_BYTES
                )
            finally:
                stream.close()
            phy, device, _ = worker.rx.validate_context(xml)
            session = worker.rx.Session(sock, phy, device, [])
            try:
                readback = {a: session.read(d, c, a) for d, c, a, _ in worker.rx.SETTINGS}
            finally:
                session.stream.close()
        job["independent_readback"] = readback
        if not all(
            worker.matches(a, readback[a], v)
            for a, v in job["baseline"].items()
            if a != "hardwaregain" or job["baseline"]["gain_control_mode"] == "manual"
        ):
            job["restoration"]["state"] = "failed"
            raise ValueError("independent_restore_mismatch")
    return job["restoration"]["state"] == "restored" and "independent_readback" in job


def run(
    config_path: Path,
    source_id: str,
    directory: Path,
    lock_root: Path,
    lock_fd: int,
    seconds: int,
    profile_path: Path | None = None,
) -> int:
    os.umask(0o077)
    config = LiveConfig.read(config_path)
    profile = (
        LiveProfile(**json.loads(profile_path.read_text()))
        if profile_path
        else config.profiles[source_id]
    )
    lock_stat = os.stat(lock_root / ".device.lock")
    fd_stat = os.fstat(lock_fd)
    if (lock_stat.st_dev, lock_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
        raise ValueError("device lock inode mismatch")
    if (lock_root / "recovery-required.json").exists():
        raise ValueError("recovery required")
    sys.path.insert(0, str(config.research_root / "src"))
    worker = importlib.import_module("receiver.worker")
    if "iq_output" not in __import__("inspect").signature(worker.capture).parameters:
        raise ValueError("research stream sink extension missing")
    directory.mkdir(parents=True, exist_ok=True)
    evidence = lock_root / ("poc-" + directory.name)
    evidence.mkdir(mode=0o700)
    stopped = threading.Event()
    finished = threading.Event()
    errors: list[str] = []
    children: list[subprocess.Popen[bytes]] = []
    started = time.monotonic()
    deadline = started + seconds
    job: dict[str, Any] = {
        "id": evidence.name,
        "state": "queued",
        "profile": "poc-live",
        "physical_channel": profile.channel,
        "baseline": {},
        "readback": {},
        "commands": [],
        "received_bytes": 0,
        "artifacts": [],
        "restoration": {"state": "not_needed", "readback": {}},
        "created_utc": worker.rx.utc(),
        "owner": "sdr-dtv-poc",
    }
    save(evidence / "job.json", job)

    def fail(code: str) -> None:
        if code not in errors:
            errors.append(code)
        stopped.set()

    def block() -> None:
        try:
            with (lock_root / "recovery-required.json").open("x") as file:
                json.dump({"reason": "poc_restore_unverified", "job_id": evidence.name}, file)
        except FileExistsError:
            pass

    def hard_stop(*_: object) -> None:
        force_stop_group(block)

    def stop(*_: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGALRM, hard_stop)
    signal.setitimer(signal.ITIMER_REAL, seconds + 25)
    raw, converted = directory / "raw.fifo", directory / "converted.fifo"
    os.mkfifo(raw, 0o600)
    os.mkfifo(converted, 0o600)
    source = config.research_root / "pocs/isdb-t-ts"
    environment = dict(os.environ)
    environment.pop("HOME", None)
    environment.pop("APPDATA", None)
    # Native Python keeps its own import path; API's virtualenv is independent.
    environment["PYTHONPATH"] = os.environ.get("SDR_NATIVE_PYTHONPATH", "/opt/wideband-build")
    ts_path = directory / f"demod/layer_{profile.layer}.ts"
    output_fd: int | None = None
    logs = []
    relay: threading.Thread | None = None
    iq_writer: threading.Thread | None = None
    iq_queue: queue.Queue[bytes | None] = queue.Queue(1024)
    startup_discarded_bytes = 0

    def launch(name: str, args: list[str]) -> subprocess.Popen[bytes]:
        log = (directory / (name + ".log")).open("xb")
        logs.append(log)
        process = subprocess.Popen(
            [str(config.native_python), *args],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=environment,
            pass_fds=(lock_fd,),
        )
        children.append(process)
        return process

    def monitor() -> None:
        last_metrics = 0.0
        while not finished.wait(0.05):
            if time.monotonic() - last_metrics >= 1:
                last_metrics = time.monotonic()
                try:
                    lines = (directory / "demod/timeline.jsonl").read_text().splitlines()
                    row = json.loads(lines[-1])
                    backlog = max(
                        0.0,
                        job["received_bytes"] / 25_600_000
                        - row["input_samples"] / (512_000_000 / 63),
                    )
                    save(
                        directory / "live-progress.json",
                        {
                            "iq_received_bytes": job["received_bytes"],
                            "startup_discarded_bytes": startup_discarded_bytes,
                            "demod_input_samples": row["input_samples"],
                            "ts_packets": row["ts_packets"],
                            "rs_omitted_words_estimate": max(0, row["rs_omitted_words"]),
                            "backlog_seconds": backlog,
                            "cpu_seconds": row["cpu_seconds"],
                            "rss_kib": row["rss_kib"],
                            "elapsed_seconds": time.monotonic() - started,
                        },
                    )
                    if backlog > 5:
                        fail("iq_backlog_limit")
                except (OSError, ValueError, IndexError, KeyError):
                    pass
            if time.monotonic() >= deadline:
                stopped.set()
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if readable and not os.read(0, 1):
                stopped.set()
            if any(p.poll() is not None for p in children) and not stopped.is_set():
                fail("native_process_ended")
            if stopped.is_set():
                return

    def relay_ts() -> None:
        nonlocal startup_discarded_bytes
        offset = 0
        os.set_blocking(1, False)
        try:
            while not ts_path.exists() and not finished.wait(0.02):
                pass
            if not ts_path.exists():
                return
            with ts_path.open("rb", buffering=0) as file:
                # Native FEC may emit a false codeword before initial lock.
                # Retain it in demod/layer_b.ts; expose TS only after five
                # consecutive aligned packets. Never resync a running stream.
                prefix = b""
                while True:
                    block = file.read(188 * 8)
                    if not block:
                        if finished.wait(0.02):
                            fail("no_ts_sync")
                            return
                        continue
                    prefix += block
                    offsets = [
                        n
                        for n in range(0, len(prefix) - 4 * 188, 188)
                        if all(prefix[n + i * 188] == 0x47 for i in range(5))
                    ]
                    if offsets:
                        offset = startup_discarded_bytes = offsets[0]
                        file.seek(offset)
                        break
                    if len(prefix) > 188 * 100:
                        fail("invalid_initial_ts")
                        return
                while True:
                    size = ts_path.stat().st_size
                    if size > 2_000_000_000 or size - offset > 32 * 1024 * 1024:
                        fail("ts_backlog_limit")
                        return
                    data = file.read(188 * 256)
                    if not data:
                        if finished.wait(0.02):
                            return
                        continue
                    view = memoryview(data)
                    until = time.monotonic() + 2
                    while view:
                        _, writable, _ = select.select([], [1], [], 0.05)
                        if writable:
                            count = os.write(1, view)
                            view = view[count:]
                            offset += count
                        elif stopped.is_set() or time.monotonic() > until:
                            if not stopped.is_set():
                                fail("ts_consumer_slow")
                            return
        except (OSError, ValueError):
            fail("ts_consumer_closed")

    def write_iq() -> None:
        assert output_fd is not None
        while (data := iq_queue.get()) is not None:
            view = memoryview(data)
            until = time.monotonic() + 1.5
            try:
                while view:
                    _, ready, _ = select.select([], [output_fd], [], 0.05)
                    if ready:
                        count = os.write(output_fd, view)
                        view = view[count:]
                    elif time.monotonic() > until:
                        fail("iq_consumer_slow")
                        break
            except OSError:
                fail("iq_consumer_closed")

    class Sink:
        def __init__(self) -> None:
            self.pending = bytearray()

        def write(self, data: bytes) -> int:
            # 1024 bounded <=64 KiB reads (64 MiB). Decouple USB cadence from
            # FIR/FFT scheduling; a full queue fails visibly and stops next RX.
            if errors:
                return len(data)
            self.pending.extend(data)
            try:
                while len(self.pending) >= 65536:
                    iq_queue.put_nowait(bytes(self.pending[:65536]))
                    del self.pending[:65536]
            except queue.Full:
                fail("iq_queue_full")
            return len(data)

        def flush(self) -> None:
            pass

        def finish(self) -> None:
            if self.pending and not errors:
                iq_queue.put(bytes(self.pending), timeout=2)

    try:
        launch(
            "demod",
            [
                str(source / "wideband/file_receiver.py"),
                str(converted),
                str(directory / "demod"),
                "--stage",
                "ts",
                "--layers",
                profile.layer,
                "--modulation-a",
                str(profile.modulation_a),
                "--rate-a",
                str(profile.rate_a),
                "--partial-reception" if profile.partial_reception else "--no-partial-reception",
                "--mode",
                str(profile.mode),
                "--gi",
                str(profile.gi),
                "--segments-a",
                str(profile.segments_a),
                "--segments-b",
                str(profile.segments_b),
                "--rate-b",
                str(profile.rate_b),
                "--interleave-a",
                str(profile.interleave_a),
                "--interleave-b",
                str(profile.interleave_b),
                "--stream-input",
                "--timing-interpolate",
            ],
        )
        launch(
            "convert",
            [
                str(source / "wideband/stream_convert_ci16.py"),
                str(raw),
                str(converted),
                "--max-samples",
                str(6_400_000 * seconds),
                "--async-fifo",
                "--progress",
                str(directory / "converter-progress.json"),
            ],
        )
        relay = threading.Thread(target=relay_ts, daemon=True)
        relay.start()
        threading.Thread(target=monitor, daemon=True).start()
        while not stopped.is_set():
            try:
                output_fd = os.open(raw, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                time.sleep(0.02)
        if output_fd is None:
            raise RuntimeError("pipeline startup stopped")
        iq_writer = threading.Thread(target=write_iq, daemon=True)
        iq_writer.start()
        worker.PROFILES["poc-live"] = {
            "frequency_hz": 473_142_857 + (profile.channel - 13) * 6_000_000,
            "sample_rate_hz": 6_400_000,
            "rf_bandwidth_hz": 6_000_000,
            "gain_db": profile.gain_db,
            "samples": 6_400_000 * seconds,
            "readbuf_samples": 1_000_000,
            "frequency_rounding_hz": 4,
            "capture_limit_seconds": seconds,
            "progress_interval_seconds": 1.0,
        }
        job["requested"] = worker.PROFILES["poc-live"]
        sink = Sink()
        worker.capture(
            evidence,
            job,
            stopped.is_set,
            lambda value: save(evidence / "job.json", value),
            iq_output=sink,
        )
        if job["state"] == "failed":
            fail("capture_failed")
        sink.finish()
        iq_queue.put(None, timeout=2)
        iq_writer.join(timeout=6)
        if iq_writer.is_alive():
            fail("iq_writer_shutdown_timeout")
        os.close(output_fd)
        output_fd = None
        # Stop monitoring expected EOF while the fixed pipeline drains.
        stopped.set()
        for child in reversed(children):
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                fail("native_shutdown_timeout")
                child.kill()
                child.wait()
        result_path = directory / "demod/result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            if result.get("tmcc", {}).get("configured_layers_match") is not True:
                fail("tmcc_profile_mismatch")
        else:
            fail("native_result_missing")
        if any(p.returncode != 0 for p in children):
            fail("native_process_failed")
        verify_restoration(worker, job)
    except Exception:
        fail("live_pipeline_failed")
        if job["restoration"]["state"] == "restored":
            job["restoration"]["state"] = "unknown"
    finally:
        stopped.set()
        if output_fd is not None:
            os.close(output_fd)
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
        finished.set()
        if relay:
            relay.join(timeout=2.5)
        for log in logs:
            log.close()
        restore = job["restoration"]["state"]
        if restore not in {"restored", "not_needed"}:
            block()
        job["pipeline_errors"] = errors
        job["state"] = "failed" if errors else job["state"]
        save(evidence / "job.json", job)
        save(
            directory / "live-result.json",
            {
                "restore": "verified"
                if restore == "restored" and "independent_readback" in job
                else "not_required"
                if restore == "not_needed"
                else "unknown",
                "errors": errors,
                "received_iq_bytes": job["received_bytes"],
                "startup_discarded_bytes": startup_discarded_bytes,
                "wall_seconds": time.monotonic() - started,
                "children_returncodes": [p.returncode for p in children],
            },
        )
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("source_id")
    parser.add_argument("directory", type=Path)
    parser.add_argument("lock_root", type=Path)
    parser.add_argument("lock_fd", type=int)
    parser.add_argument("seconds", type=int, choices=range(1, 601))
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    sys.exit(
        run(
            args.config,
            args.source_id,
            args.directory,
            args.lock_root,
            args.lock_fd,
            args.seconds,
            args.profile,
        )
    )


if __name__ == "__main__":
    main()
