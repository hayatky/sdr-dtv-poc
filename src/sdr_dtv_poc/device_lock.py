# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatible flock boundary; no device I/O and no automatic recovery override."""

import fcntl
import json
import os
from decimal import Decimal
from pathlib import Path
from uuid import UUID


class DeviceBusy(Exception):
    pass


class DeviceLock:
    def __init__(self, root: Path):
        self.root = root
        self.fd: int | None = None

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / ".device.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (self.root / "recovery-required.json").exists():
                raise DeviceBusy("restore_unverified")
            for folder in self.root.iterdir():
                record = folder / "job.json"
                if not folder.is_dir() or folder.is_symlink() or not record.is_file():
                    continue
                try:
                    job = json.loads(record.read_text())
                    if job.get("state") not in {"completed", "cancelled", "failed"}:
                        raise DeviceBusy("restore_unverified")
                    if job.get("restoration", {}).get("state") in {"pending", "unknown", "failed"}:
                        recovery = json.loads((folder / "recovery.json").read_text())
                        baseline, actual = job["baseline"], recovery["after"]
                        if (
                            recovery["capture_id"] != job["id"]
                            or recovery["state"] != "restored_readback_verified"
                            or recovery["baseline"] != baseline
                            or set(actual) != set(baseline)
                        ):
                            raise DeviceBusy("restore_unverified")
                        for name, expected in baseline.items():
                            if name == "hardwaregain" and baseline["gain_control_mode"] != "manual":
                                continue
                            matches = (
                                actual[name] == expected
                                if name == "gain_control_mode"
                                else (
                                    Decimal(actual[name].split()[0]) == Decimal(expected.split()[0])
                                )
                            )
                            if not matches:
                                raise DeviceBusy("restore_unverified")
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                    raise DeviceBusy("restore_unverified") from None
        except BlockingIOError:
            os.close(fd)
            raise DeviceBusy("device_busy") from None
        except Exception:
            os.close(fd)
            raise
        self.fd = fd

    def block(self, job_id: UUID) -> None:
        # Never replace the research tool's evidence. Its recovery tool must not
        # treat a PoC record as a research job with a verified baseline.
        marker = self.root / "recovery-required.json"
        try:
            with marker.open("x") as file:
                json.dump({"job_id": str(job_id), "reason": "poc_restore_unverified"}, file)
        except FileExistsError:
            pass

    def release(self) -> None:
        if self.fd is not None:
            # close, not LOCK_UN: an inherited worker FD must retain ownership.
            os.close(self.fd)
            self.fd = None
