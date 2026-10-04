# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit recovery jobs, serialized with scans and viewing and the shared lock."""

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from .device_lock import DeviceBusy
from .models import Restore, Scan, Session, State

if TYPE_CHECKING:
    from .manager import Manager


class Recovery:
    def __init__(self, manager: "Manager"):
        self.manager = manager
        self.state = "idle"
        self.error_code: str | None = None
        self.task: asyncio.Task[None] | None = None

    def required(self) -> bool:
        m = self.manager
        items: list[Session | Scan] = [*m.sessions.values(), *m.scans.items.values()]
        if (m.device.root / "recovery-required.json").exists() or any(
            item.restore in {Restore.unknown, Restore.failed}
            or (
                item.restore == Restore.pending
                and item.state not in {State.starting, State.running, State.stopping}
            )
            for item in items
        ):
            return True
        # An active local receiver is expected to have a pending hardware job.
        # Inspect abandoned/shared jobs once local reception has stopped.
        if any(task and not task.done() for task in (m.task, m.scans.task)):
            return False
        try:
            return bool(m.device.pending_jobs())
        except (DeviceBusy, OSError):
            return True

    def status(self) -> dict[str, object]:
        return {"state": self.state, "error_code": self.error_code, "required": self.required()}

    def start(self) -> dict[str, object]:
        from .manager import Conflict, Unavailable

        m = self.manager
        if self.task and not self.task.done():
            return self.status()
        if not m.settings.live:
            raise Unavailable("live_not_implemented")
        m.check_idle(ignore_restore=True)
        try:
            m.device.acquire(recovery=True)
        except DeviceBusy as exc:
            raise Conflict(str(exc)) from None
        self.state, self.error_code = "running", None
        self.task = asyncio.create_task(self.run())
        return self.status()

    def reconcile(self) -> None:
        m = self.manager
        # Require a valid shared recovery proof for every unresolved API row.
        sessions: list[Session] = []
        scans: list[Scan] = []
        for item in m.sessions.values():
            if item.restore in {Restore.pending, Restore.unknown, Restore.failed}:
                folder = m.device.root / f"poc-{item.id}"
                job = json.loads((folder / "job.json").read_text())
                if not m.device.verified(folder, job):
                    raise ValueError("recovery_evidence_missing")
                sessions.append(item)
        for scan in m.scans.items.values():
            if scan.restore in {Restore.pending, Restore.unknown, Restore.failed}:
                folders = list(m.device.root.glob(f"poc-{scan.id}-*"))
                if not folders:
                    raise ValueError("recovery_evidence_missing")
                for folder in folders:
                    job = json.loads((folder / "job.json").read_text())
                    if not m.device.verified(folder, job):
                        raise ValueError("recovery_evidence_missing")
                scans.append(scan)
        # Persist together before changing in-memory rows; retain failed/partial
        # status and all historical hardware job records.
        with m.store.db:
            for item in sessions:
                updated = item.model_copy(update={"restore": Restore.verified})
                m.store.db.execute(
                    "UPDATE sessions SET body=? WHERE id=?",
                    (updated.model_dump_json(), str(item.id)),
                )
            for scan in scans:
                updated_scan = scan.model_copy(update={"restore": Restore.verified})
                m.store.db.execute(
                    "UPDATE scans SET body=? WHERE id=?",
                    (updated_scan.model_dump_json(), str(scan.id)),
                )
        restored_items: list[Session | Scan] = [*sessions, *scans]
        for restored in restored_items:
            restored.restore = Restore.verified

    async def run(self) -> None:
        m = self.manager
        process: asyncio.subprocess.Process | None = None
        try:
            # After a crash between verified proof and DB update, use that proof;
            # never replay device writes or clear a marker without a worker.
            if m.device.pending_jobs() or (m.device.root / "recovery-required.json").exists():
                assert m.settings.live and m.device.fd is not None
                directory = m.settings.data_dir / "recovery" / str(uuid4())
                directory.mkdir(parents=True)
                result: Path = directory / "result.json"
                process = await asyncio.create_subprocess_exec(
                    str(m.settings.live.native_python),
                    "-m",
                    "sdr_dtv_poc.child_exec",
                    str(os.getpid()),
                    str(m.settings.live.native_python),
                    "-m",
                    "sdr_dtv_poc.recovery_worker",
                    str(m.settings.live.config_path),
                    str(m.device.root),
                    str(result),
                    str(m.device.fd),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    pass_fds=(m.device.fd,),
                )
                async with asyncio.timeout(30):
                    code = await process.wait()
                report = json.loads(result.read_text())
                if code != 0 or report["state"] != "completed":
                    raise ValueError(report.get("error_code") or "recovery_failed")
            self.reconcile()
            if self.required() or m.device.pending_jobs():
                raise ValueError("recovery_failed")
            self.state = "completed"
        except Exception as exc:
            self.state = "failed"
            self.error_code = (
                str(exc)
                if str(exc)
                in {"board_unreachable", "settings_changed", "recovery_evidence_missing"}
                else "recovery_failed"
            )
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            m.device.release()

    async def close(self) -> None:
        if self.task:
            await self.task
