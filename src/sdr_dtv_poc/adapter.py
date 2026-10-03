# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import sys
from typing import Protocol

from .config import Source
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
