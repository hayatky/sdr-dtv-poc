# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate only original test patterns and a sine wave; never reads broadcast data."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/demo/demo.ts"))
    args = parser.parse_args()
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.with_suffix(".json").exists():
        parser.error("output already exists; choose a new location")
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=25",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        "8",
        "-c:v",
        "mpeg2video",
        "-b:v",
        "500k",
        "-threads",
        "1",
        "-c:a",
        "mp2",
        "-b:a",
        "96k",
        "-muxrate",
        "1000000",
        "-metadata",
        "service_provider=SDR Demo",
        "-metadata",
        "service_name=Synthetic Test",
        "-f",
        "mpegts",
        str(output),
    ]
    subprocess.run(command, check=True, timeout=60)
    version = subprocess.run(
        ["ffmpeg", "-version"], capture_output=True, text=True, check=True, timeout=5
    ).stdout.splitlines()[0]
    output.with_suffix(".json").write_text(
        json.dumps(
            {
                "input_kind": "synthetic",
                "license": "GPL-3.0-or-later",
                "generator": "scripts/generate-demo.py",
                "ffmpeg": version,
                "duration_seconds": 8,
                "muxrate": 1_000_000,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "size_bytes": output.stat().st_size,
                "video": "testsrc2 320x180 25fps MPEG-2",
                "audio": "440Hz sine 48kHz MP2",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
