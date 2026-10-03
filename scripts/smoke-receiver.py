# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the fixed native graph with finite synthetic silence, without devices.

This checks graph construction, file input and EOF, not successful demodulation.
Build the separate receiver image using docs/receiver.md before running this script.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, default=Path("data/receiver-build"))
    args = parser.parse_args()
    source = args.build_dir.resolve() / "files"
    manifest = json.loads((source.parent / "manifest.json").read_text())
    for name, digest in manifest["sha256"].items():
        path = source / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit("Prepared receiver source hash mismatch")

    with tempfile.TemporaryDirectory(prefix="sdr-native-check-") as temporary:
        work = Path(temporary)
        iq = work / "silence.cf32"
        iq.write_bytes(bytes(8 * 1_048_576))
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--tmpfs",
                "/tmp:size=32m,mode=1777",
                "-v",
                f"{source}:/receiver:ro",
                "-v",
                f"{work}:/check:rw",
                "sdr-dtv-native-wideband:issue3",
                "timeout",
                "30",
                # GNU Radio 3.10 uses HOME for FFTW wisdom. Without HOME/APPDATA
                # its appdata_path falls back to /tmp, the bounded writable tmpfs.
                "env",
                "-u",
                "HOME",
                "-u",
                "APPDATA",
                "python3",
                "/receiver/wideband/file_receiver.py",
                "/check/silence.cf32",
                "/check/output",
                "--stage",
                "ts",
                "--layers",
                "b",
                "--gi",
                "0.125",
            ],
            capture_output=True,
            timeout=45,
        )
        report_path = work / "output/result.json"
        if not report_path.is_file():
            raise SystemExit("Native check failed before creating a result report")
        report = json.loads(report_path.read_text())
        expected_hash = hashlib.sha256(iq.read_bytes()).hexdigest()
        if not (
            result.returncode == 1
            and report["status"] == "unverified_no_valid_tmcc"
            and report["input"]["sha256"] == expected_hash
            and report["input"]["samples"] == 1_048_576
            and report["tmcc"]["valid_frames"] == 0
            and report["output"]["bytes"] == 0
        ):
            # Do not print native logs or exception text that may contain local paths.
            raise SystemExit("Native check did not produce the expected no-signal result")
        print(
            json.dumps(
                {
                    "check": "native_graph_and_eof",
                    "input_kind": "synthetic_silence",
                    "receiver_exit_code": result.returncode,
                    "receiver_status": report["status"],
                    "input_samples": report["input"]["samples"],
                    "input_sha256": expected_hash,
                    "valid_tmcc_frames": 0,
                    "output_bytes": 0,
                    "wall_seconds": report["wall_seconds"],
                    "max_rss_kib": report["max_rss_kib"],
                }
            )
        )


if __name__ == "__main__":
    main()
