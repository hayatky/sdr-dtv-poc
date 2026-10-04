# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded channel discovery using pinned research CP/TMCC implementations."""

import argparse
import importlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .live_config import LiveConfig, LiveProfile
from .live_worker import force_stop_group, save, verify_restoration

SAMPLES = 5_760_000  # 0.9 seconds; 23.04 MB of diagnostic IQ per channel.


def profile_from_tmcc(channel: int, mode: int, gi: float, frame: dict[str, Any]) -> LiveProfile:
    layers = frame["current_layers"]
    a, b, c = (layers[k] for k in "ABC")
    if (
        frame.get("parity_valid")
        and a["segments"] == 13
        and a["modulation"] in ("QPSK", "16QAM", "64QAM")
        and all(
            x["modulation"] == "unused" and x["code_rate"] == "unused" and x["segments_code"] == 15
            for x in (b, c)
        )
    ):
        return LiveProfile(
            channel=channel,
            mode=mode,
            gi=gi,
            rate_b=0,
            interleave_a=a["interleave_length"],
            interleave_b=0,
            segments_a=13,
            segments_b=0,
            layer="a",
            modulation_a={"QPSK": 4, "16QAM": 16, "64QAM": 64}[a["modulation"]],
            rate_a=("1/2", "2/3", "3/4", "5/6", "7/8").index(a["code_rate"]),
            partial_reception=bool(frame["partial_reception"]),
        )
    if not (
        frame.get("parity_valid")
        and a["modulation"] == "QPSK"
        and a["code_rate"] == "2/3"
        and b["modulation"] == "64QAM"
        and c["modulation"] == "unused"
        and c["code_rate"] == "unused"
        and c["segments_code"] == 15
    ):
        raise ValueError("unsupported_tmcc")
    return LiveProfile(
        channel=channel,
        mode=mode,
        gi=gi,
        rate_b=("1/2", "2/3", "3/4", "5/6", "7/8").index(b["code_rate"]),
        interleave_a=a["interleave_length"],
        interleave_b=b["interleave_length"],
        segments_a=a["segments"],
        segments_b=b["segments"],
        partial_reception=bool(frame.get("partial_reception", True)),
    )


def analyze(
    config: LiveConfig, directory: Path, channel: int, stopped: threading.Event
) -> dict[str, Any]:
    # Native scientific dependencies stay outside the API virtual environment.
    np = importlib.import_module("numpy")
    resample_poly = importlib.import_module("scipy.signal").resample_poly

    source = config.research_root / "pocs/isdb-t-ts"
    sys.path[:0] = [str(source), str(source / "wideband")]
    converter = importlib.import_module("stream_convert_ci16")
    cp = importlib.import_module("local_cp")
    converted = directory / "input.cf32"
    conversion = converter.convert(directory / "input.iq", converted, 630_000, SAMPLES)
    wide = np.fromfile(converted, dtype="<c8")
    # CP analysis expects exactly 64e6/63 samples/s. This is rate conversion,
    # not a new synchronizer or TMCC decoder.
    narrow = resample_poly(wide, 1, 8)
    candidates = cp.probe_array(narrow[:262144])["groups"]
    candidates.sort(
        key=lambda g: sum(w["contrast"] for w in g["windows"]) / len(g["windows"]), reverse=True
    )
    save(directory / "cp.json", {"groups": candidates, "conversion": conversion})
    environment = dict(os.environ)
    environment.pop("HOME", None)
    environment.pop("APPDATA", None)
    environment["PYTHONPATH"] = os.environ.get("SDR_NATIVE_PYTHONPATH", "/opt/wideband-build")
    attempts = []
    for index, candidate in enumerate(candidates):
        if stopped.is_set():
            return {"state": "cancelled"}
        target = directory / f"tmcc-{index}"
        args = [
            str(config.native_python),
            str(source / "wideband/file_receiver.py"),
            str(converted),
            str(target),
            "--stage",
            "tmcc",
            "--mode",
            str(candidate["mode"]),
            "--gi",
            str(candidate["gi"]),
            "--timing-interpolate",
        ]
        with (directory / f"tmcc-{index}.log").open("xb") as log:
            child = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment
            )
            deadline = time.monotonic() + 3
            try:
                while (
                    child.poll() is None and not stopped.wait(0.05) and time.monotonic() < deadline
                ):
                    pass
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
        report_path = target / "result.json"
        attempts.append(
            {"mode": candidate["mode"], "gi": candidate["gi"], "returncode": child.returncode}
        )
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text())
        frames = report.get("tmcc", {}).get("frames", [])
        if len(frames) < 2:
            continue
        if not all(
            f.get("parity_valid") and f["current_layers"] == frames[0]["current_layers"]
            for f in frames
        ):
            return {"state": "unstable_tmcc", "attempts": attempts}
        try:
            profile = profile_from_tmcc(channel, candidate["mode"], candidate["gi"], frames[0])
        except (KeyError, TypeError, ValueError):
            return {"state": "unsupported_tmcc", "attempts": attempts}
        return {
            "state": "tmcc_detected",
            "profile": asdict(profile),
            "valid_frames": len(frames),
            "attempts": attempts,
        }
    return {"state": "not_detected", "attempts": attempts}


def run(
    config_path: Path, channel: int, directory: Path, lock_root: Path, lock_fd: int, seconds: int
) -> int:
    os.umask(0o077)
    config = LiveConfig.read(config_path)
    if not 13 <= channel <= 52:
        raise ValueError("invalid channel")
    actual, owned = os.stat(lock_root / ".device.lock"), os.fstat(lock_fd)
    if (actual.st_dev, actual.st_ino) != (owned.st_dev, owned.st_ino):
        raise ValueError("device lock inode mismatch")
    if (lock_root / "recovery-required.json").exists():
        raise ValueError("recovery required")
    sys.path.insert(0, str(config.research_root / "src"))
    worker = importlib.import_module("receiver.worker")
    directory.mkdir(parents=True, exist_ok=True)
    evidence = lock_root / ("poc-" + directory.name)
    evidence.mkdir(mode=0o700)
    job: dict[str, Any] = {
        "id": evidence.name,
        "state": "queued",
        "profile": "poc-discovery",
        "physical_channel": channel,
        "baseline": {},
        "readback": {},
        "commands": [],
        "received_bytes": 0,
        "artifacts": [],
        "restoration": {"state": "not_needed", "readback": {}},
        "created_utc": worker.rx.utc(),
        "owner": "sdr-dtv-poc",
    }
    stopped = threading.Event()
    finished = threading.Event()
    restored = False
    errors = []
    result: dict[str, Any] = {"state": "failed"}

    def block() -> None:
        try:
            with (lock_root / "recovery-required.json").open("x") as file:
                json.dump(
                    {"reason": "poc_discovery_restore_unverified", "job_id": evidence.name}, file
                )
        except FileExistsError:
            pass

    def hard_stop(*_: object) -> None:
        force_stop_group(lambda: None if restored else block())

    def monitor() -> None:
        while not finished.wait(0.05):
            readable, _, _ = select.select([sys.stdin], [], [], 0)
            if readable and not os.read(0, 1):
                stopped.set()
                return

    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGALRM, hard_stop)
    signal.setitimer(signal.ITIMER_REAL, seconds + 15)
    threading.Thread(target=monitor, daemon=True).start()
    worker.PROFILES["poc-discovery"] = {
        "frequency_hz": 473_142_857 + (channel - 13) * 6_000_000,
        "sample_rate_hz": 6_400_000,
        "rf_bandwidth_hz": 6_000_000,
        "gain_db": 20,
        "samples": SAMPLES,
        "readbuf_samples": 1_000_000,
        "frequency_rounding_hz": 4,
        "capture_limit_seconds": 8,
    }
    job["requested"] = worker.PROFILES["poc-discovery"]
    save(evidence / "job.json", job)
    try:
        with (directory / "input.iq").open("xb") as output:
            worker.capture(
                evidence,
                job,
                stopped.is_set,
                lambda value: save(evidence / "job.json", value),
                iq_output=output,
            )
        restored = verify_restoration(worker, job)
        # Record restoration before CPU-only analysis. Analysis failure must not
        # erase successful independent RX readback or allow another capture early.
        save(evidence / "job.json", job)
        save(
            directory / "live-result.json",
            {"restore": "verified" if restored else "unknown", "errors": []},
        )
        if job["state"] == "failed" or not restored:
            raise RuntimeError("capture_failed")
        if stopped.is_set():
            result = {"state": "cancelled"}
        elif (directory / "input.iq").stat().st_size != SAMPLES * 4:
            raise RuntimeError("incomplete_capture")
        else:
            result = analyze(config, directory, channel, stopped)
    except Exception:
        errors.append("discovery_failed")
    finally:
        finished.set()
        if not restored and job["restoration"]["state"] != "not_needed":
            block()
        save(evidence / "job.json", job)
        save(directory / "discovery.json", result)
        save(
            directory / "live-result.json",
            {
                "restore": "verified"
                if restored
                else "not_required"
                if job["restoration"]["state"] == "not_needed"
                else "unknown",
                "errors": errors,
            },
        )
        signal.setitimer(signal.ITIMER_REAL, 0)
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("channel", type=int)
    parser.add_argument("directory", type=Path)
    parser.add_argument("lock_root", type=Path)
    parser.add_argument("lock_fd", type=int)
    parser.add_argument("seconds", type=int, choices=range(1, 61))
    args = parser.parse_args()
    sys.exit(
        run(args.config, args.channel, args.directory, args.lock_root, args.lock_fd, args.seconds)
    )


if __name__ == "__main__":
    main()
