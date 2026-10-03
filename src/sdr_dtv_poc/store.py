# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
from pathlib import Path

from .models import Artifact, Session


class Store:
    """One API process owns this connection; schema upgrades are transactional."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise RuntimeError("unsupported database schema")
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
            self.db.execute("PRAGMA user_version=1")

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
                    (str(item.id), relative, item.model_dump_json()),
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

    def close(self) -> None:
        self.db.close()
