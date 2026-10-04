# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded TS branch and supervised FFmpeg conversion, independent of recording."""

import asyncio
import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .artifacts import NAME, validate_playlist
from .config import Settings
from .models import Artifact, MediaStatus
from .store import Store

ENCODING_PROFILE = "h264-1080-2997-8m-v1"


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


async def spawn(
    *args: str, stdin: int = asyncio.subprocess.PIPE, stdout: int = asyncio.subprocess.DEVNULL
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sdr_dtv_poc.child_exec",
        str(os.getpid()),
        *args,
        stdin=stdin,
        stdout=stdout,
        stderr=asyncio.subprocess.DEVNULL,
    )


class Media:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        owner: UUID,
        service_id: int,
        status: MediaStatus,
        changed: Callable[[], None],
        *,
        vod: bool = False,
    ):
        self.settings, self.store = settings, store
        self.status, self.changed = status, changed
        self.owner, self.service_id, self.vod = owner, service_id, vod
        self.directory = settings.data_dir / ("playback" if vod else "hls") / str(owner)
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(settings.media_queue_chunks)
        self.process: asyncio.subprocess.Process | None = None
        self.cas: asyncio.subprocess.Process | None = None
        self.feeder: asyncio.Task[None] | None = None
        self.monitor: asyncio.Task[None] | None = None
        self.artifact: Artifact | None = None
        self.closing = False
        self.closed = False

    def fail(self, code: str, stage: str = "conversion") -> None:
        if self.status.state == "failed":
            return
        self.status.state = "failed"
        self.status.error_code, self.status.error_stage = code, stage
        self.status.url = None
        if self.process and self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        if self.cas and self.cas.returncode is None:
            try:
                self.cas.kill()
            except ProcessLookupError:
                pass
        try:
            if self.artifact:
                self.artifact.partial = True
                self.store.update_artifact(self.artifact)
            self.changed()
        except sqlite3.Error:
            self.status.error_code, self.status.error_stage = "database_error", "storage"

    async def start(self) -> None:
        read_fd = write_fd = None
        try:
            self.directory.mkdir(parents=True)
            if self.settings.cas_executable:
                read_fd, write_fd = os.pipe()
            self.process = await spawn(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-n",
                "-probesize",
                "500000",
                "-analyzeduration",
                "1000000",
                "-f",
                "mpegts",
                "-i",
                "pipe:0",
                "-map",
                f"0:p:{self.service_id}:v:0",
                "-map",
                f"0:p:{self.service_id}:a:0",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-filter_threads",
                "2",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                "-vf",
                "bwdif=mode=send_frame:parity=auto:deint=interlaced",
                "-r",
                "30000/1001",
                "-g",
                "60",
                "-keyint_min",
                "60",
                "-sc_threshold",
                "0",
                "-b:v",
                "8000k",
                "-maxrate",
                "12000k",
                "-bufsize",
                "24000k",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-f",
                "hls",
                "-hls_time",
                "2",
                "-hls_list_size",
                "0" if self.vod else "6",
                "-hls_delete_threshold",
                "2",
                "-hls_flags",
                "temp_file+independent_segments" + ("" if self.vod else "+delete_segments"),
                "-hls_segment_filename",
                str(self.directory / "segment_%06d.ts"),
                str(self.directory / "index.m3u8"),
                stdin=read_fd if read_fd is not None else asyncio.subprocess.PIPE,
            )
            if self.settings.cas_executable:
                assert write_fd is not None
                self.cas = await spawn(
                    str(self.settings.cas_executable), "-m0", "-p0", "-s0", "-v0", stdout=write_fd
                )
            self.feeder = asyncio.create_task(self.feed())
            self.monitor = asyncio.create_task(self.watch())
        except OSError:
            self.fail("converter_unavailable")
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)

    def offer(self, data: bytes) -> None:
        if self.status.state == "failed" or not data:
            return
        # No CAS is configured. Preserve original TS but do not send scrambled
        # payload to the decoder or label this as an RF/recording failure.
        if not self.cas and any(data[n + 3] & 0xC0 for n in range(0, len(data), 188)):
            self.fail("cas_unavailable", "cas")
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            self.fail("downstream_slow", "transport")

    async def feed(self) -> None:
        input_process = self.cas or self.process
        assert input_process and input_process.stdin
        try:
            while (data := await self.queue.get()) is not None:
                input_process.stdin.write(data)
                await asyncio.wait_for(input_process.stdin.drain(), 2)
            input_process.stdin.close()
        except TimeoutError:
            self.fail("downstream_slow", "transport")
        except (OSError, RuntimeError):
            self.fail("cas_failed", "cas") if self.cas else self.fail("converter_failed")

    def publish(self) -> None:
        playlist = self.directory / "index.m3u8"
        # Read the atomically replaced playlist FIRST. A subsequent directory
        # snapshot can include the segments it references; an expiry race is
        # retried. The exact bytes and member list are then committed together.
        data = None
        if playlist.is_file():
            with playlist.open("rb") as file:
                data = file.read(65537)
        paths = list(self.directory.iterdir())
        try:
            size = sum(p.stat().st_size for p in paths if p.is_file())
        except FileNotFoundError:
            return
        limit = self.settings.max_playback_bytes if self.vod else self.settings.max_hls_bytes
        if size > limit or len(paths) > (512 if self.vod else 16):
            self.fail("hls_output_limit", "storage")
            return
        if shutil.disk_usage(self.directory).free < self.settings.min_free_bytes:
            self.fail("storage_full", "storage")
            return
        if data is None or self.status.state == "failed":
            return
        members = sorted(p.name for p in paths if NAME.fullmatch(p.name))
        references = [
            line for line in data.decode("utf-8").splitlines() if line and not line.startswith("#")
        ]
        if any(NAME.fullmatch(line) and line not in members for line in references):
            return
        validate_playlist(data, members)
        if not references:
            return
        if self.artifact is None:
            self.artifact = Artifact(
                id=uuid4(),
                session_id=self.owner,
                kind="hls",
                media_type="application/vnd.apple.mpegurl",
                size_bytes=size,
                sha256="",
                members=members,
                playlist_snapshot=data.decode("utf-8"),
            )
            self.store.save_artifact(
                self.artifact, str(playlist.relative_to(self.settings.data_dir))
            )
            self.status.artifact_id = self.artifact.id
            self.status.url = f"/api/artifacts/{self.artifact.id}/files/index.m3u8"
            self.status.ready_at = timestamp()
            self.status.state = "ready"
            self.changed()
        elif self.artifact.members != members or self.artifact.playlist_snapshot != data.decode(
            "utf-8"
        ):
            self.artifact.members, self.artifact.size_bytes = members, size
            self.artifact.playlist_snapshot = data.decode("utf-8")
            self.store.update_artifact(self.artifact)

    async def watch(self) -> None:
        assert self.process
        try:
            while self.process.returncode is None:
                if self.cas and self.cas.returncode is not None and not self.closing:
                    self.fail("cas_failed", "cas")
                    return
                self.publish()
                await asyncio.sleep(0.1)
            if not self.closing and self.status.state != "failed":
                self.fail("converter_exited")
        except Exception:
            self.fail("hls_publish_failed", "storage")

    async def close(self) -> None:
        if self.closed:
            return
        self.closing = True
        try:
            if self.process:
                if self.status.state != "failed":
                    async with asyncio.timeout(5):
                        await self.queue.put(None)
                        if self.feeder:
                            await self.feeder
                        if self.cas:
                            await self.cas.wait()
                            if self.cas.returncode != 0:
                                self.fail("cas_failed", "cas")
                        await self.process.wait()
                    if self.process.returncode != 0:
                        self.fail("converter_failed")
                    else:
                        self.publish()
                        if self.status.ready_at is None:
                            self.fail("no_playable_stream")
                        elif self.status.state != "failed":
                            self.status.state = "completed"
                            self.changed()
        except TimeoutError:
            self.fail("converter_stop_timeout", "cleanup")
        except Exception:
            self.fail("hls_publish_failed", "storage")
        finally:
            if self.cas:
                if self.cas.returncode is None:
                    try:
                        self.cas.kill()
                    except ProcessLookupError:
                        pass
                await self.cas.wait()
            if self.process and self.process.returncode is None:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            if self.process:
                await self.process.wait()
            for task in (self.feeder, self.monitor):
                if task:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (self.feeder, self.monitor) if t), return_exceptions=True
            )
            if self.status.state == "failed" and self.artifact:
                self.artifact.partial = True
                self.store.update_artifact(self.artifact)
            # A cancelled cleanup must remain retryable until children and
            # background tasks have actually been reaped.
            self.closed = True
