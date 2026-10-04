# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded RX-setting recovery. Never opens an RX buffer or starts acquisition."""

import importlib
import json
import os
import signal
import socket
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from .device_lock import DeviceLock
from .live_config import LiveConfig
from .live_worker import save


def restore(worker: Any, job: dict[str, Any], report: dict[str, Any]) -> None:
    rx = worker.rx
    fields = {a for _, _, a, _ in rx.SETTINGS}
    baseline, configured = job["baseline"], job["readback"]
    if set(baseline) != fields or set(configured) != fields:
        raise ValueError("recovery_evidence_missing")
    for values in (baseline, configured):
        if values["gain_control_mode"] not in {"manual", "slow_attack", "fast_attack", "hybrid"}:
            raise ValueError("recovery_evidence_missing")
        for name in fields - {"gain_control_mode"}:
            value = Decimal(values[name].split()[0])
            if not value.is_finite() or not 0 <= value <= 6_000_000_000:
                raise ValueError("recovery_evidence_missing")

    def connect() -> tuple[Any, Any, dict[str, str]]:
        sock = socket.create_connection((rx.HOST, 30431), timeout=2)
        sock.settimeout(2)
        stream = sock.makefile("rb")
        try:
            _, xml = rx.probe.fetch_payload(sock, stream, "PRINT", rx.probe.MAX_XML_BYTES)
            phy, device, _ = rx.validate_context(xml)
            session = rx.Session(sock, phy, device, report["commands"])
            try:
                actual = {a: session.read(d, c, a) for d, c, a, _ in rx.SETTINGS}
            except BaseException:
                session.stream.close()
                raise
            return sock, session, actual
        except BaseException:
            sock.close()
            raise
        finally:
            stream.close()

    try:
        rx.check_route()
        sock, session, before = connect()
    except (OSError, ValueError):
        raise ValueError("board_unreachable") from None
    compare = fields - ({"hardwaregain"} if baseline["gain_control_mode"] != "manual" else set())
    report["before"] = before
    try:
        if not all(worker.matches(a, before[a], baseline[a]) for a in compare):
            # Allow a partially restored job, but no arbitrary/unrelated settings.
            if not all(
                worker.matches(a, before[a], baseline[a])
                or worker.matches(a, before[a], configured[a])
                for a in compare
            ):
                raise ValueError("settings_changed")
            for d, c, a, _ in rx.SETTINGS:
                if a == "gain_control_mode" or a not in compare:
                    continue
                session.write(d, c, a, baseline[a].split()[0])
            session.write("INPUT", "voltage0", "gain_control_mode", baseline["gain_control_mode"])
    finally:
        session.stream.close()
        sock.close()
    # A second connection must read back the baseline, including read-only recovery.
    sock, session, after = connect()
    session.stream.close()
    sock.close()
    report["after"] = after
    if not all(worker.matches(a, after[a], baseline[a]) for a in compare):
        raise ValueError("recovery_failed")


def run(config_path: Path, root: Path, output: Path, fd: int) -> None:
    import hashlib

    signal.alarm(25)
    config = LiveConfig.read(config_path)
    actual, inherited = (root / ".device.lock").stat(), os.fstat(fd)
    if (actual.st_dev, actual.st_ino) != (inherited.st_dev, inherited.st_ino):
        raise ValueError("recovery_evidence_missing")
    lock = DeviceLock(root)
    pending = lock.pending_jobs()
    marker = root / "recovery-required.json"
    marker_bytes = marker.read_bytes() if marker.exists() else None
    if not pending and marker_bytes:
        marker_id = json.loads(marker_bytes).get("job_id", "")
        for candidate in root.iterdir():
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            name = candidate.name.removeprefix("poc-")
            if marker_id not in {candidate.name, name, name[:36]}:
                continue
            record = json.loads((candidate / "job.json").read_text())
            if lock.verified(candidate, record):
                pending.append((candidate, record))
    if len(pending) != 1:
        raise ValueError("recovery_evidence_missing")
    folder, job = pending[0]
    if job.get("owner") != "sdr-dtv-poc" or job.get("id") != folder.name:
        raise ValueError("recovery_evidence_missing")
    if marker_bytes:
        marker_id = json.loads(marker_bytes).get("job_id", "")
        name = folder.name.removeprefix("poc-")
        if marker_id not in {name, folder.name, name[:36]}:
            raise ValueError("recovery_evidence_missing")
    sys.path.insert(0, str(config.research_root / "src"))
    worker = importlib.import_module("receiver.worker")
    report = {
        "capture_id": job["id"],
        "baseline": job["baseline"],
        "state": "pending",
        "commands": [],
        "started_at": worker.rx.utc(),
        "job_sha256": hashlib.sha256((folder / "job.json").read_bytes()).hexdigest(),
    }
    attempt = folder / f"recovery-attempt-{uuid4()}.json"
    save(attempt, report)
    try:
        restore(worker, job, report)
        report.update(state="restored_readback_verified", ended_at=worker.rx.utc())
        save(attempt, report)
        proof = folder / "recovery.json"
        if proof.exists():
            proof.rename(folder / f"recovery-previous-{uuid4()}.json")
        save(proof, report)
        if lock.pending_jobs():
            raise ValueError("recovery_failed")
        if marker_bytes is not None:
            if marker.read_bytes() != marker_bytes:
                raise ValueError("recovery_failed")
            marker.rename(folder / f"recovery-marker-{uuid4()}.json")
        save(output, {"state": "completed", "error_code": None})
    except Exception:
        if report["state"] == "pending":
            report["state"] = "failed"
            save(attempt, report)
        raise


def main() -> None:
    output = Path(sys.argv[3])
    try:
        run(Path(sys.argv[1]), Path(sys.argv[2]), output, int(sys.argv[4]))
    except Exception as exc:
        code = str(exc)
        if code not in {"board_unreachable", "settings_changed", "recovery_evidence_missing"}:
            code = "recovery_failed"
        save(output, {"state": "failed", "error_code": code})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
