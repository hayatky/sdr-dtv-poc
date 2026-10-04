# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from .models import Artifact, Session, StrictModel


class Store:
    """One API process owns this connection; schema upgrades are transactional."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.db.close()
            raise RuntimeError("unsupported database schema")
        if version == 1:
            backup = path.with_name("state.before-v2.sqlite3")
            if backup.exists():
                self.db.close()
                raise RuntimeError("schema backup already exists")
            with sqlite3.connect(backup) as destination:
                self.db.backup(destination)
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS sessions "
                "(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, "
                "request_json TEXT NOT NULL, body TEXT NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS artifacts "
                "(id TEXT PRIMARY KEY, relative_path TEXT NOT NULL, body TEXT NOT NULL)"
            )
            for table in ("scans", "recordings", "playbacks", "services"):
                self.db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, body TEXT NOT NULL, request_json TEXT NOT NULL)"
                )
            self.db.execute("PRAGMA user_version=2")

    def save_session(self, session: Session, request_json: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (str(session.id), str(session.request_id), request_json, session.model_dump_json()),
            )

    def finish(self, session: Session, artifact: tuple[Artifact, str] | None) -> None:
        with self.db:
            if artifact:
                item, relative = artifact
                self.db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?)",
                    (str(item.model_dump()["id"]), relative, item.model_dump_json()),
                )
            self.db.execute(
                "UPDATE sessions SET body=? WHERE id=?",
                (session.model_dump_json(), str(session.id)),
            )

    def sessions(self) -> list[tuple[Session, str]]:
        return [
            (Session.model_validate_json(body), request)
            for body, request in self.db.execute("SELECT body,request_json FROM sessions")
        ]

    def save_artifact(self, artifact: Artifact, relative_path: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO artifacts VALUES (?,?,?)",
                (str(artifact.id), relative_path, artifact.model_dump_json()),
            )

    def artifact(self, artifact_id: str) -> tuple[Artifact, str] | None:
        row = self.db.execute(
            "SELECT body,relative_path FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return (Artifact.model_validate_json(row[0]), row[1]) if row else None

    def records(self, table: str) -> list[tuple[str, str]]:
        assert table in {"scans", "recordings", "playbacks", "services"}
        return list(self.db.execute(f"SELECT body,request_json FROM {table}"))

    def save_record(
        self,
        table: str,
        item: BaseModel,
        request: StrictModel | None = None,
        artifact: tuple[Artifact, str] | None = None,
        revoke_artifact_ids: tuple[str, ...] = (),
    ) -> None:
        assert table in {"scans", "recordings", "playbacks", "services"}
        with self.db:
            for artifact_id in revoke_artifact_ids:
                self.db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
            if artifact:
                value, relative = artifact
                self.db.execute(
                    "INSERT INTO artifacts VALUES (?,?,?)",
                    (str(value.id), relative, value.model_dump_json()),
                )
            self.db.execute(
                f"INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (
                    str(item.model_dump()["id"]),
                    item.model_dump_json(),
                    request.model_dump_json() if request else "",
                ),
            )

    def update_artifact(self, artifact: Artifact) -> None:
        with self.db:
            self.db.execute(
                "UPDATE artifacts SET body=? WHERE id=?",
                (artifact.model_dump_json(), str(artifact.id)),
            )

    def close(self) -> None:
        self.db.close()
