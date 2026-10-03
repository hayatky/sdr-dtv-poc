# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import Settings, Source
from .models import STOP_GRACE, Restore


class Adapter(Protocol):
    async def start(self) -> None: ...
    async def read(self) -> bytes: ...
    async def stop(self) -> Restore: ...


class FileWorkerFailed(Exception):
    """The file worker failed; it does not own device settings to restore."""


class FileAdapter:
    """The same packet stream boundary will be used by the future live adapter."""

    def __init__(self, source: Source):
        self.source = source
        self.lock_fd: int | None = None
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "sdr_dtv_poc.worker",
            str(self.source.path),
            "--bitrate",
            str(self.source.bitrate),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            pass_fds=(self.lock_fd,) if self.lock_fd is not None else (),
        )

    async def read(self) -> bytes:
        assert self.process and self.process.stdout
        data = await self.process.stdout.read(188 * 7)
        if not data and await self.process.wait() != 0:
            raise FileWorkerFailed("worker_failed")
        return data

    async def stop(self) -> Restore:
        if self.process:
            forced = False
            if self.process.stdin:
                self.process.stdin.close()
            if self.process.returncode is None:
                try:
                    await asyncio.wait_for(self.process.wait(), STOP_GRACE / 2)
                except TimeoutError:
                    forced = True
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                    await self.process.wait()
            if forced or self.process.returncode != 0:
                raise FileWorkerFailed("worker_failed")
        return Restore.not_required


class LiveWorkerFailed(Exception):
    pass


class LiveAdapter:
    def __init__(
        self, settings: "Settings", source: Source, directory: "Path", seconds: int, lock_fd: int
    ):
        self.settings, self.source, self.directory = settings, source, directory
        self.seconds, self.lock_fd = seconds, lock_fd
        self.process: asyncio.subprocess.Process | None = None
        self.log: BinaryIO | None = None
        self.failed = False

    async def start(self) -> None:
        assert self.settings.live and self.settings.device_lock_dir
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log = (self.directory / "supervisor.log").open("xb")
        self.process = await asyncio.create_subprocess_exec(
            str(self.settings.live.native_python),
            "-m",
            "sdr_dtv_poc.live_worker",
            str(self.settings.live.config_path),
            str(self.source.live_id),
            str(self.directory),
            str(self.settings.device_lock_dir),
            str(self.lock_fd),
            str(self.seconds),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.log,
            pass_fds=(self.lock_fd,),
            start_new_session=True,
        )

    async def read(self) -> bytes:
        assert self.process and self.process.stdout
        data = await self.process.stdout.read(188 * 7)
        if not data and await self.process.wait() != 0:
            self.failed = True
            raise LiveWorkerFailed("live_worker_failed")
        return data

    def request_stop(self) -> None:
        if self.process and self.process.stdin:
            self.process.stdin.close()

    def metrics(self) -> dict[str, float | int | str]:
        try:
            result: dict[str, float | int | str] = json.loads(
                (self.directory / "live-progress.json").read_text()
            )
            return result
        except (OSError, ValueError):
            return {}

    async def stop(self) -> Restore:
        forced = False
        drain: asyncio.Task[None] | None = None
        if self.process:
            self.request_stop()

            async def discard_tail() -> None:
                assert self.process and self.process.stdout
                while await self.process.stdout.read(65536):
                    pass

            drain = asyncio.create_task(discard_tail())
            if self.process.returncode is None:
                try:
                    await asyncio.wait_for(self.process.wait(), 20)
                except TimeoutError:
                    forced = True
                    os.killpg(self.process.pid, signal.SIGKILL)
                    await self.process.wait()
            self.failed = forced or self.process.returncode != 0
        if drain:
            await drain
        if self.log:
            self.log.close()
        result = self.directory / "live-result.json"
        if forced or not result.is_file():
            return Restore.unknown
        record = json.loads(result.read_text())
        self.failed = self.failed or bool(record.get("errors"))
        return Restore(record["restore"])
