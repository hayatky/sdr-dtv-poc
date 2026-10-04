# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatible flock boundary; no device I/O and no automatic recovery override."""

import fcntl
import hashlib
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

    def acquire(self, *, recovery: bool = False) -> None:
        if self.fd is not None:
            raise DeviceBusy("device_busy")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / ".device.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not recovery:
                if (self.root / "recovery-required.json").exists() or self.pending_jobs():
                    raise DeviceBusy("restore_unverified")
        except BlockingIOError:
            os.close(fd)
            raise DeviceBusy("device_busy") from None
        except Exception:
            os.close(fd)
            raise
        self.fd = fd

    @staticmethod
    def baseline_matches(baseline: object, actual: object) -> bool:
        try:
            fields = {
                "sampling_frequency",
                "rf_bandwidth",
                "frequency",
                "gain_control_mode",
                "hardwaregain",
            }
            if (
                not isinstance(baseline, dict)
                or not isinstance(actual, dict)
                or set(baseline) != fields
                or set(actual) != fields
            ):
                return False
            for name, expected in baseline.items():
                if name == "hardwaregain" and baseline["gain_control_mode"] != "manual":
                    continue
                if name == "gain_control_mode":
                    if actual[name] != expected:
                        return False
                else:
                    a, b = Decimal(actual[name].split()[0]), Decimal(expected.split()[0])
                    if not a.is_finite() or not b.is_finite() or a != b:
                        return False
            return True
        except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
            return False

    @classmethod
    def verified(cls, folder: Path, job: dict[str, object]) -> bool:
        restoration = job.get("restoration", {})
        return cls.recovered(folder, job) or (
            isinstance(restoration, dict)
            and restoration.get("state") == "restored"
            and job.get("state") in {"completed", "cancelled", "failed"}
            and cls.baseline_matches(job.get("baseline"), job.get("independent_readback"))
        )

    @staticmethod
    def recovered(folder: Path, job: dict[str, object]) -> bool:
        try:
            proof = json.loads((folder / "recovery.json").read_text())
            baseline = job["baseline"]
            if not isinstance(baseline, dict) or not baseline:
                return False
            if job.get("state") not in {"completed", "cancelled", "failed"} and (
                proof.get("job_sha256")
                != hashlib.sha256((folder / "job.json").read_bytes()).hexdigest()
            ):
                return False
            actual = proof["after"]
            if job.get("owner") == "sdr-dtv-poc" and not DeviceLock.baseline_matches(
                baseline, actual
            ):
                return False
            if (
                proof["capture_id"] != job["id"]
                or proof["state"] != "restored_readback_verified"
                or proof["baseline"] != baseline
                or set(actual) != set(baseline)
            ):
                return False
            for name, expected in baseline.items():
                if name == "hardwaregain" and baseline["gain_control_mode"] != "manual":
                    continue
                if name == "gain_control_mode":
                    if actual[name] != expected:
                        return False
                elif Decimal(actual[name].split()[0]) != Decimal(expected.split()[0]):
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            return False

    def pending_jobs(self) -> list[tuple[Path, dict[str, object]]]:
        pending: list[tuple[Path, dict[str, object]]] = []
        if not self.root.exists():
            return pending
        for folder in self.root.iterdir():
            record = folder / "job.json"
            if not folder.is_dir() or folder.is_symlink() or not record.is_file():
                continue
            try:
                job = json.loads(record.read_text())
                unresolved = (
                    job.get("state") not in {"completed", "cancelled", "failed"}
                    or job.get("restoration", {}).get("state") in {"pending", "unknown", "failed"}
                    or (
                        job.get("owner") == "sdr-dtv-poc"
                        and job.get("restoration", {}).get("state") == "restored"
                        and not self.verified(folder, job)
                    )
                )
                if unresolved and not self.recovered(folder, job):
                    pending.append((folder, job))
            except (OSError, ValueError, KeyError, TypeError):
                raise DeviceBusy("restore_unverified") from None
        return pending

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
