# SPDX-License-Identifier: GPL-3.0-or-later
"""Product-UI-independent finite synthetic API/A/V validation against a dedicated server."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://localhost:18324")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    origin: str = args.origin
    token = ""

    def call(path: str, body: dict[str, Any] | None = None) -> Any:
        req = Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Origin": origin, "X-CSRF-Token": token, "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def wait(path: str, predicate: Any, seconds: int = 30) -> Any:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            item = call(path)
            if predicate(item):
                return item
            time.sleep(0.1)
        raise RuntimeError("validation deadline exceeded")

    def decode(url: str, duration: int | None = None) -> None:
        command = ["ffmpeg", "-nostdin", "-v", "error", "-i", url]
        if duration:
            command += ["-t", str(duration)]
        command += ["-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
        result = subprocess.run(command, capture_output=True, timeout=30)
        if result.returncode or result.stderr:
            raise RuntimeError("audio/video decode failed or emitted errors")

    token = call("/api/bootstrap")["csrf_token"]
    scan = call("/api/scans", {"request_id": str(uuid4()), "channels": [13, 14, 15]})
    scan = wait(f"/api/scans/{scan['id']}", lambda x: x["state"] in {"completed", "failed"})
    assert scan["state"] == "completed" and scan["completed_channels"] == 3
    services = call("/api/services")
    selected = [s for s in services if s["physical_channel"] in (13, 14)]
    assert len(selected) == 2 and len({s["transport_stream_id"] for s in selected}) == 2
    service = next(s for s in selected if s["physical_channel"] == 13)
    began = time.monotonic()
    session = call(
        "/api/sessions",
        {"request_id": str(uuid4()), "service_key": service["id"], "duration_seconds": 600},
    )
    session_path = f"/api/sessions/{session['id']}"
    try:
        session = wait(session_path, lambda x: x["hls"]["state"] in {"ready", "failed"})
        assert session["state"] == "running" and session["hls"]["state"] == "ready"
        ready_seconds = time.monotonic() - began
        record = call(
            "/api/recordings",
            {"request_id": str(uuid4()), "session_id": session["id"], "duration_seconds": 8},
        )
        record_path = f"/api/recordings/{record['id']}"
        decode(origin + session["hls"]["url"], 4)
        record = wait(record_path, lambda x: x["state"] != "running")
        assert record["state"] == "completed" and record["end_reason"] == "deadline"
        assert call(session_path)["state"] == "running"
        with urlopen(origin + record["download_url"], timeout=10) as response:
            original = response.read()
        assert len(original) == record["bytes_written"] and len(original) % 188 == 0
        if args.source:
            with args.source.open("rb") as file:
                file.seek(record["source_offset_bytes"])
                assert file.read(len(original)) == original
        artifact = call(f"/api/artifacts/{record['artifact_id']}")
        assert artifact["sha256"] == hashlib.sha256(original).hexdigest()
        playback = call(record_path + "/playback", {})
        assert call(record_path + "/playback", {})["id"] == playback["id"]
        playback = wait(record_path + "/playback", lambda x: x["state"] in {"completed", "failed"})
        assert playback["state"] == "completed"
        decode(origin + playback["url"])
        with urlopen(origin + record["download_url"], timeout=10) as response:
            assert response.read() == original
        with tempfile.TemporaryDirectory(prefix="sdr-stage2-decode-") as directory:
            source = Path(directory) / "original.ts"
            source.write_bytes(original)
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                check=True,
                timeout=10,
            )
            media_seconds = float(json.loads(result.stdout)["format"]["duration"])
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "scan_channels": 3,
                    "services": 2,
                    "hls_ready_seconds": round(ready_seconds, 3),
                    "recording_elapsed_seconds": record["elapsed_seconds"],
                    "recording_media_seconds": media_seconds,
                    "recording_bytes": len(original),
                    "original_unchanged": True,
                    "live_and_recorded_av_decode": "passed",
                    "human_playback": "not_checked",
                },
                indent=2,
            )
        )
    finally:
        call(session_path + "/stop", {})
        wait(session_path, lambda x: x["state"] in {"completed", "failed"})


if __name__ == "__main__":
    main()
