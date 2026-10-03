# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the synthetic API over HTTP; print only IDs and aggregate results."""

import argparse
import hashlib
import json
import time
import urllib.request
from typing import Any
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://localhost:8000")
    parser.add_argument("--session-id", help="check an existing session after restart")
    args = parser.parse_args()

    def request(path: str, data: dict[str, Any] | None = None) -> Any:
        headers = {"Origin": args.origin}
        if data is not None:
            headers.update({"X-CSRF-Token": token, "Content-Type": "application/json"})
        req = urllib.request.Request(
            args.origin + path,
            headers=headers,
            data=json.dumps(data).encode() if data is not None else None,
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read())

    assert request("/api/health")["status"] == "ok"
    with urllib.request.urlopen(args.origin + "/", timeout=5) as response:
        assert response.status == 200
    assert request("/api/diagnostics")["checks"]["board"]["status"] == "not_required"
    token = request("/api/bootstrap")["csrf_token"]
    if args.session_id:
        session = request("/api/sessions/" + args.session_id)
    else:
        session = request(
            "/api/sessions",
            {"request_id": str(uuid4()), "duration_seconds": 8, "enable_hls": False},
        )
        limit = time.monotonic() + 20
        while session["state"] in {"starting", "running", "stopping"}:
            if time.monotonic() > limit:
                raise RuntimeError("demo deadline exceeded")
            time.sleep(0.1)
            session = request("/api/sessions/" + session["id"])
    assert session["state"] == "completed" and session["end_reason"] in {"eof", "deadline"}, session
    assert session["input_kind"] == "synthetic"
    artifact = request("/api/artifacts/" + session["artifact_id"])
    with urllib.request.urlopen(
        args.origin + "/api/artifacts/" + artifact["id"] + "/download", timeout=5
    ) as response:
        data = response.read()
    assert len(data) == artifact["size_bytes"] and len(data) % 188 == 0
    assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
    print(
        json.dumps(
            {
                "session_id": session["id"],
                "state": session["state"],
                "bytes": len(data),
                "sha256": artifact["sha256"],
                "input_kind": "synthetic",
            }
        )
    )


if __name__ == "__main__":
    main()
