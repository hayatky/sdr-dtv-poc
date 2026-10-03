# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import hashlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sdr_dtv_poc.adapter import FileAdapter
from sdr_dtv_poc.app import create_app
from sdr_dtv_poc.config import Settings, Source
from sdr_dtv_poc.manager import Conflict, Manager
from sdr_dtv_poc.models import Artifact, EndReason, Restore, SessionStart, Stage, State

ORIGIN = "http://localhost:8000"
PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    source = tmp_path / "demo.ts"
    source.write_bytes(PACKET * 700)
    return Settings(data_dir=tmp_path / "app", demo_path=source, demo_bitrate=188 * 8 * 1000)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        yield client


def headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/bootstrap").json()["csrf_token"]}


def start(client: TestClient, **values: Any) -> dict[str, Any]:
    response = client.post(
        "/api/sessions", json={"request_id": str(uuid4()), **values}, headers=headers(client)
    )
    assert response.status_code == 202, response.text
    result: dict[str, Any] = response.json()
    return result


def terminal(client: TestClient, session_id: str) -> dict[str, Any]:
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        result: dict[str, Any] = client.get(f"/api/sessions/{session_id}").json()
        if result["state"] in {"completed", "failed", "interrupted"}:
            return result
        time.sleep(0.01)
    raise AssertionError("session did not terminate")


def test_eof_artifact_and_restart(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        for name in ("app.js", "api.js", "mock.js"):
            script = client.get(f"/static/{name}")
            assert script.headers["content-type"].startswith("text/javascript")
            assert "innerHTML" not in script.text and "v-html" not in script.text
        assert client.get("/static/style.css").headers["content-type"].startswith("text/css")
        assert client.get("/static/entry.js").status_code == 404
        assert client.get("/api/diagnostics").json()["starts_receiver"] is False
        assert client.get("/api/sessions").json() == []
        session = terminal(client, start(client)["id"])
        assert session["state"] == "completed"
        assert session["end_reason"] == "eof"
        assert session["input_kind"] == "synthetic"
        artifact = session["artifact_id"]
        response = client.get(f"/api/artifacts/{artifact}/download")
        assert response.content == settings.demo_path.read_bytes()
        assert (
            client.get(f"/api/artifacts/{artifact}").json()["sha256"]
            == hashlib.sha256(response.content).hexdigest()
        )
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.get(f"/api/sessions/{session['id']}").json() == session
        assert client.get(f"/api/artifacts/{artifact}/download").status_code == 200


def test_duplicate_start_stop_and_conflicts(client: TestClient) -> None:
    request_id = str(uuid4())
    first = start(client, request_id=request_id)
    same = start(client, request_id=request_id)
    assert same["id"] == first["id"]
    for payload in (
        {"request_id": str(uuid4())},
        {"request_id": request_id, "duration_seconds": 1},
    ):
        assert (
            client.post("/api/sessions", json=payload, headers=headers(client)).status_code == 409
        )
    for _ in range(2):
        assert (
            client.post(f"/api/sessions/{first['id']}/stop", headers=headers(client)).status_code
            == 202
        )
    result = terminal(client, first["id"])
    assert result["state"] == "completed"
    assert result["end_reason"] == "requested"
    assert (
        client.post(f"/api/sessions/{first['id']}/stop", headers=headers(client)).json() == result
    )


@pytest.mark.parametrize(
    "override",
    [
        {"Host": "evil.example"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"X-CSRF-Token": "wrong"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_rejected_operations_have_no_side_effects(
    client: TestClient, override: dict[str, str]
) -> None:
    assert (
        client.post(
            "/api/sessions",
            json={"request_id": str(uuid4())},
            headers={**headers(client), **override},
        ).status_code
        == 403
    )
    assert client.get("/api/sessions").json() == []


def test_missing_origin_token_and_validation(client: TestClient) -> None:
    assert client.post("/api/sessions", json={"request_id": str(uuid4())}).status_code == 403
    token_only = {"X-CSRF-Token": headers(client)["X-CSRF-Token"]}
    assert client.post("/api/sessions", json={}, headers=token_only).status_code == 403
    assert client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.get("/api/bootstrap", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    for data in (
        {"duration_seconds": 601},
        {"source_id": "../private"},
        {"command": "private-value"},
        {"source_id": "<script>bad()</script>"},
    ):
        response = client.post(
            "/api/sessions", json={"request_id": str(uuid4()), **data}, headers=headers(client)
        )
        assert response.status_code == 422
        assert "private" not in response.text and "<script>" not in response.text
    response = client.post(
        "/api/sessions",
        json={"request_id": str(uuid4()), "input_kind": "live"},
        headers=headers(client),
    )
    assert response.status_code == 501
    assert client.get("/api/sessions").json() == []
    assert client.get("/api/recordings").status_code == 501
    schema = client.get("/openapi.json").json()
    assert (
        schema["components"]["schemas"]["SessionStart"]["properties"]["duration_seconds"]["maximum"]
        == 600
    )


def test_artifact_symlinks_partial_and_hls(client: TestClient, settings: Settings) -> None:
    directory = settings.data_dir / "hls"
    directory.mkdir()
    (directory / "index.m3u8").write_text("#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n")
    (directory / "segment.ts").write_bytes(PACKET)
    artifact = Artifact(
        id=uuid4(),
        session_id=uuid4(),
        kind="hls",
        media_type="application/vnd.apple.mpegurl",
        size_bytes=1,
        sha256="unused",
        members=["index.m3u8", "segment.ts"],
    )
    app = client.app
    assert client.portal

    def register() -> None:
        app.state.manager.store.save_artifact(artifact, "hls/index.m3u8")  # type: ignore[attr-defined]

    client.portal.call(register)
    base = f"/api/artifacts/{artifact.id}/files/"
    assert client.get(base + "index.m3u8").status_code == 200
    assert client.get(base + "segment.ts").content == PACKET
    assert client.get(base + "unknown.ts").status_code == 404
    for reference in ("../secret.ts", "https://evil.example/a.ts", "/secret.ts"):
        (directory / "index.m3u8").write_text(f"#EXTM3U\n{reference}\n")
        assert client.get(base + "index.m3u8").status_code == 404
    (directory / "segment.ts").unlink()
    (directory / "segment.ts").symlink_to(settings.demo_path)
    assert client.get(base + "segment.ts").status_code == 404
    directory.rename(settings.data_dir / "elsewhere")
    directory.symlink_to(settings.data_dir / "elsewhere", target_is_directory=True)
    assert client.get(base + "segment.ts").status_code == 404
    partial = Artifact(
        id=uuid4(),
        session_id=uuid4(),
        kind="source_ts",
        media_type="video/mp2t",
        size_bytes=1,
        sha256="unused",
        partial=True,
    )

    def register_partial() -> None:
        app.state.manager.store.save_artifact(partial, "../demo.ts")  # type: ignore[attr-defined]

    client.portal.call(register_partial)
    assert client.get(f"/api/artifacts/{partial.id}/download").status_code == 404


class FakeAdapter:
    def __init__(self, *, fault: bool = False, restore: Restore = Restore.not_required):
        self.fault = fault
        self.restore = restore
        self.stopped = False

    async def start(self) -> None:
        pass

    async def read(self) -> bytes:
        if self.fault:
            raise RuntimeError("sensitive failure details")
        return PACKET

    async def stop(self) -> Restore:
        self.stopped = True
        return self.restore


@pytest.mark.parametrize("mode", ["deadline", "failure", "restore", "limit"])
def test_adapter_failures_deadline_and_cleanup(settings: Settings, mode: str) -> None:
    async def exercise() -> None:
        adapter = FakeAdapter(
            fault=mode == "failure",
            restore=Restore.unknown if mode == "restore" else Restore.not_required,
        )
        tick = 0.0

        def clock() -> float:
            nonlocal tick
            tick += 1
            return tick

        if mode == "limit":
            from dataclasses import replace

            config = replace(settings, max_output_bytes=188)
        else:
            config = settings
        manager = Manager(config, adapter_factory=lambda _: adapter, clock=clock)
        try:
            session = manager.start(SessionStart(request_id=uuid4(), duration_seconds=10))
            assert manager.task
            await manager.task
            assert adapter.stopped
            if mode == "deadline":
                assert session.end_reason == EndReason.deadline
                assert session.state == State.completed
            else:
                assert session.state == State.failed and session.partial
                assert session.artifact_id is None
            if mode == "restore":
                with pytest.raises(Conflict, match="restore_unverified"):
                    manager.start(SessionStart(request_id=uuid4()))
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_restart_recovery_and_process_lock(settings: Settings) -> None:
    async def exercise() -> None:
        manager = Manager(settings)
        with pytest.raises(RuntimeError, match="another API"):
            Manager(settings)
        session = manager.start(SessionStart(request_id=uuid4()))
        await manager.close()
        assert session.partial and session.end_reason == EndReason.server_shutdown
        # Simulate metadata surviving an abrupt server death, without creating an orphan worker.
        manager = Manager(settings)
        session.state = State.running
        manager.persist(session)
        await manager.close()
        manager = Manager(settings)
        restored = manager.sessions[session.id]
        assert restored.state == State.interrupted
        assert restored.end_reason == EndReason.server_restart
        assert manager.task is None
        await manager.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("phase", ["start", "finish"])
def test_database_failure_never_reports_completion(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import sqlite3

    from sdr_dtv_poc.manager import Unavailable

    async def exercise() -> None:
        adapter = FakeAdapter()
        manager = Manager(settings, adapter_factory=lambda _: adapter)

        def fail(*args: Any) -> None:
            raise sqlite3.OperationalError("disk full")

        request = SessionStart(request_id=uuid4(), duration_seconds=1)
        try:
            if phase == "start":
                monkeypatch.setattr(manager.store, "save_session", fail)
                with pytest.raises(Unavailable, match="database_error"):
                    manager.start(request)
                assert not manager.sessions and manager.task is None
                with pytest.raises(Unavailable):
                    manager.start(request)
            else:
                monkeypatch.setattr(manager.store, "finish", fail)
                session = manager.start(request)
                assert manager.task
                manager.stop(session.id)
                await manager.task
                assert session.state == State.failed and session.partial
                assert session.artifact_id is None and manager.active_id is None
                assert session.end_reason == EndReason.database_error
                assert adapter.stopped
                assert manager.store.sessions()[0][0].state == State.failed
                with pytest.raises(Unavailable):
                    manager.start(SessionStart(request_id=uuid4()))
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_artifact_transaction_rolls_back(settings: Settings) -> None:
    async def exercise() -> None:
        manager = Manager(settings)
        manager.store.db.execute("""
            CREATE TRIGGER fail_completion BEFORE UPDATE ON sessions
            WHEN json_extract(NEW.body, '$.state') = 'completed'
            BEGIN SELECT RAISE(ABORT, 'injected database failure'); END
        """)
        try:
            session = manager.start(SessionStart(request_id=uuid4()))
            assert manager.task
            await manager.task
            assert session.state == State.failed and session.partial
            assert session.artifact_id is None
            assert manager.store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
            assert manager.store.sessions()[0][0].state == State.failed
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_saved_input_is_registered_and_labeled(settings: Settings) -> None:
    from dataclasses import replace

    from sdr_dtv_poc.config import Source

    config = replace(
        settings, saved_sources={"local_sample": Source(settings.demo_path, settings.demo_bitrate)}
    )
    with TestClient(create_app(config), base_url=ORIGIN) as client:
        session = terminal(
            client, start(client, input_kind="saved_ts", source_id="local_sample")["id"]
        )
        assert session["input_kind"] == "saved_ts" and session["state"] == "completed"
        assert str(settings.demo_path) not in str(session)
        response = client.post(
            "/api/sessions",
            json={
                "request_id": str(uuid4()),
                "input_kind": "saved_ts",
                "source_id": "unregistered",
            },
            headers=headers(client),
        )
        assert response.status_code == 503
        assert response.json()["code"] == "source_not_registered"


def test_non_ascii_token_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/sessions",
        json={"request_id": str(uuid4())},
        headers={b"origin": ORIGIN.encode(), b"x-csrf-token": b"\xff"},
    )
    assert response.status_code == 403
    assert client.get("/api/sessions").json() == []


@pytest.mark.parametrize("failure", ["nonzero_exit", "forced_exit", "early_exit"])
def test_file_worker_stop_failure_is_partial(settings: Settings, failure: str) -> None:
    class StopFailureAdapter(FileAdapter):
        async def start(self) -> None:
            tail = {
                "nonzero_exit": "sys.stdin.buffer.read(); sys.exit(7)",
                "forced_exit": "time.sleep(30)",
                "early_exit": "sys.exit(7)",
            }[failure]
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                f"import os, sys, time; os.write(1, {PACKET!r}); {tail}",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

    async def exercise() -> None:
        adapter = StopFailureAdapter(Source(settings.demo_path, settings.demo_bitrate))
        manager = Manager(settings, adapter_factory=lambda _: adapter)
        try:
            session = manager.start(SessionStart(request_id=uuid4()))
            async with asyncio.timeout(5):
                while not session.bytes_received or (
                    failure == "early_exit" and session.state != State.failed
                ):
                    await asyncio.sleep(0.01)
            manager.stop(session.id)
            assert manager.task
            await asyncio.wait_for(manager.task, 8)
            assert adapter.process and adapter.process.returncode not in (None, 0)
            assert session.state == State.failed and session.partial
            assert session.end_reason == EndReason.worker_failed
            assert session.error_stage == (
                Stage.transport if failure == "early_exit" else Stage.cleanup
            )
            assert session.restore == Restore.not_required
            assert session.artifact_id is None
            assert manager.store.sessions()[0][0] == session
            assert manager.store.db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        finally:
            await manager.close()

    asyncio.run(exercise())
