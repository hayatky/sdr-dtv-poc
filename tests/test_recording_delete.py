# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from sdr_dtv_poc.app import create_app
from sdr_dtv_poc.config import Settings
from sdr_dtv_poc.manager import Conflict, Manager
from sdr_dtv_poc.media import ENCODING_PROFILE
from sdr_dtv_poc.models import Artifact, InputKind, Playback, Recording, State
from sdr_dtv_poc.store import Store

ORIGIN = "http://localhost:8000"


def seed(tmp_path: Path) -> tuple[Settings, Recording, list[Playback]]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir()
    record = Recording(
        id=uuid4(),
        request_id=uuid4(),
        session_id=uuid4(),
        input_kind=InputKind.synthetic,
        state=State.completed,
        started_at="2026-10-04T00:00:00Z",
        deadline_at="2026-10-04T00:05:00Z",
        duration_seconds=300,
        bytes_written=188,
        artifact_id=uuid4(),
    )
    directory = settings.data_dir / "recordings" / str(record.id)
    directory.mkdir(parents=True)
    (directory / "original.ts").write_bytes(b"\x47" + b"\x00" * 187)
    record.download_url = f"/api/recordings/{record.id}/download"
    store = Store(settings.data_dir / "state.sqlite3")
    assert record.artifact_id
    artifact = Artifact(
        id=record.artifact_id,
        session_id=record.session_id,
        kind="source_ts",
        media_type="video/mp2t",
        size_bytes=188,
        sha256="test",
    )
    store.save_record(
        "recordings", record, artifact=(artifact, f"recordings/{record.id}/original.ts")
    )
    playbacks = []
    for _ in range(2):
        p = Playback(
            id=uuid4(),
            recording_id=record.id,
            state="completed",
            started_at=record.started_at,
            deadline_at=record.deadline_at,
            artifact_id=uuid4(),
        )
        directory = settings.data_dir / "playback" / str(p.id)
        directory.mkdir(parents=True)
        (directory / "index.m3u8").write_text("#EXTM3U\n#EXT-X-ENDLIST\n")
        assert p.artifact_id
        artifact = Artifact(
            id=p.artifact_id,
            session_id=record.session_id,
            kind="hls",
            media_type="application/vnd.apple.mpegurl",
            size_bytes=24,
            sha256="",
            members=["index.m3u8"],
        )
        store.save_record("playbacks", p, artifact=(artifact, f"playback/{p.id}/index.m3u8"))
        playbacks.append(p)
    store.close()
    (settings.data_dir / "session-original.ts").write_text("keep")
    return settings, record, playbacks


def headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.get("/api/bootstrap").json()["csrf_token"]}


def test_delete_protected_idempotent_revokes_all_profiles_and_survives_restart(
    tmp_path: Path,
) -> None:
    settings, r, ps = seed(tmp_path)
    path = f"/api/recordings/{r.id}/delete"
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.post(path).status_code == 403
        assert client.post(path, headers=headers(client)).status_code == 200
        assert client.post(path, headers=headers(client)).status_code == 200
        assert client.get("/api/recordings").json() == []
        for url in [
            f"/api/recordings/{r.id}",
            f"/api/recordings/{r.id}/download",
            f"/api/artifacts/{r.artifact_id}/download",
            *(f"/api/artifacts/{p.artifact_id}/files/index.m3u8" for p in ps),
        ]:
            assert client.get(url).status_code == 404
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.get("/api/recordings").json() == []
        assert client.post(path, headers=headers(client)).status_code == 200
    assert not (settings.data_dir / "recordings" / str(r.id)).exists()
    assert all(not (settings.data_dir / "playback" / str(p.id)).exists() for p in ps)
    assert (settings.data_dir / "session-original.ts").read_text() == "keep"


def test_cleanup_failure_is_retryable_after_restart_without_following_parent_link(
    tmp_path: Path,
) -> None:
    settings, r, _ = seed(tmp_path)
    parent = settings.data_dir / "recordings"
    parent.rename(settings.data_dir / "held-recordings")
    parent.symlink_to(settings.data_dir / "held-recordings", target_is_directory=True)
    path = f"/api/recordings/{r.id}/delete"
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.post(path, headers=headers(client)).status_code == 503
        assert client.get("/api/recordings").json()[0]["deletion_pending"]
        assert client.get(f"/api/artifacts/{r.artifact_id}/download").status_code == 404
    assert (parent / str(r.id) / "original.ts").is_file()
    parent.unlink()
    (settings.data_dir / "held-recordings").rename(parent)
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        assert client.post(path, headers=headers(client)).status_code == 200
        assert client.get("/api/recordings").json() == []


def test_database_failure_rolls_back_revocation_and_preserves_files(tmp_path: Path) -> None:
    settings, r, _ = seed(tmp_path)
    app = create_app(settings)
    with sqlite3.connect(settings.data_dir / "state.sqlite3") as db:
        db.execute(
            "CREATE TRIGGER fail_delete BEFORE UPDATE ON recordings "
            "BEGIN SELECT RAISE(ABORT, 'test'); END"
        )
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(f"/api/recordings/{r.id}/delete", headers=headers(client))
        assert response.status_code == 503
        assert client.get(f"/api/recordings/{r.id}/download").status_code == 200
    assert (settings.data_dir / "recordings" / str(r.id) / "original.ts").is_file()


def test_running_recording_and_failed_playback_still_cleaning_up_block_delete(
    tmp_path: Path,
) -> None:
    settings, r, _ = seed(tmp_path)

    async def scenario() -> None:
        manager = Manager(settings)
        record = manager.recordings.items[r.id]
        record.state = State.running
        with pytest.raises(Conflict, match="recording_busy"):
            manager.recordings.delete(r.id)
        record.state = State.completed
        manager.recordings.playbacks[r.id].state = "failed"
        released = asyncio.Event()
        manager.recordings.playback_owner = r.id
        manager.recordings.playback_task = asyncio.create_task(released.wait())  # type: ignore[arg-type]
        with pytest.raises(Conflict, match="playback_busy"):
            manager.recordings.delete(r.id)
        released.set()
        await manager.recordings.playback_task
        manager.recordings.delete(r.id)
        await manager.close()

    asyncio.run(scenario())


def test_old_quality_cache_gets_a_new_playback_without_touching_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, r, ps = seed(tmp_path)

    async def scenario() -> None:
        manager = Manager(settings)

        async def finish(*args: object) -> None:
            return None

        monkeypatch.setattr(manager.recordings, "convert", finish)
        playback = manager.recordings.start_playback(r.id)
        assert playback.id not in {p.id for p in ps}
        assert playback.encoding_profile == ENCODING_PROFILE
        assert manager.recordings.start_playback(r.id).id == playback.id
        assert (settings.data_dir / "recordings" / str(r.id) / "original.ts").stat().st_size == 188
        assert manager.recordings.playback_task
        await manager.recordings.playback_task
        await manager.close()

    asyncio.run(scenario())
