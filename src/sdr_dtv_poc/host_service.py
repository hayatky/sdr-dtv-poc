# SPDX-License-Identifier: GPL-3.0-or-later
"""Compose-only host preparation. Never imported or invoked by the HTTP API.

Only the single verified RNDIS interface is configured. No RF commands, host
service management, firmware changes, Docker socket, or sudo are involved.
"""

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .device_lock import DeviceBusy, DeviceLock

ADDRESS = "192.168.2.10/24"
BOARD = "192.168.2.1"


def ip(*args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["ip", "-j", *args], check=True, capture_output=True, text=True, timeout=5
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def find_interface(root: Path = Path("/sys/bus/usb/devices")) -> str:
    boards = [
        p
        for p in root.iterdir()
        if (p / "idVendor").is_file()
        and (p / "idProduct").is_file()
        and (p / "idVendor").read_text().strip() == "0456"
        and (p / "idProduct").read_text().strip() == "b673"
    ]
    if len(boards) != 1:
        raise RuntimeError("expected_one_sdr_board: connect exactly one supported SDR board")
    interfaces = list(root.glob(boards[0].name + ":*/net/*"))
    if len(interfaces) != 1:
        raise RuntimeError("expected_one_rndis_interface: check the board USB connection")
    return interfaces[0].name


def initialize_directory(path: Path, uid: int, gid: int) -> None:
    if path.is_symlink():
        raise RuntimeError("symlink_directory_rejected")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.stat()
    if stat.st_uid == 0 and not any(path.iterdir()):
        os.chown(path, uid, gid)
        path.chmod(0o700)
    elif (stat.st_uid, stat.st_gid) != (uid, gid):
        raise RuntimeError("directory_owner_mismatch: set SDR_UID/SDR_GID to the existing owner")


class Network:
    def __init__(self, state: Path):
        self.state = state
        self.interface = ""
        self.identity: dict[str, Any] = {}
        self.owned = False

    def prepare(self) -> None:
        self.interface = find_interface()
        rows = ip("address", "show", "dev", self.interface)
        if len(rows) != 1:
            raise RuntimeError("rndis_address_unavailable")
        row = rows[0]
        self.identity = {key: row[key] for key in ("ifindex", "ifname", "address")}
        addresses = [a for a in row.get("addr_info", []) if a.get("family") == "inet"]
        if self.state.exists():
            previous = json.loads(self.state.read_text())
            if previous != self.identity or not self.is_our_address(addresses):
                raise RuntimeError("host_restore_unverified: inspect the previous connection state")
            # Resume our own temporary route after a helper restart. The API's
            # independent RX recovery gate remains intact.
            self.owned = True
        else:
            if addresses or "UP" in row.get("flags", []):
                raise RuntimeError("rndis_in_use: existing interface settings were preserved")
            routes = ip("route", "show", "table", "all")
            if any(r.get("dst") == "192.168.2.0/24" for r in routes):
                raise RuntimeError("board_subnet_in_use: existing routes were preserved")
            with self.state.open("x") as file:
                json.dump(self.identity, file)
            self.owned = True
            ip("address", "add", ADDRESS, "dev", self.interface)
            ip("link", "set", "dev", self.interface, "up")
        route = ip("route", "get", BOARD)
        if (
            len(route) != 1
            or route[0].get("dev") != self.interface
            or route[0].get("prefsrc") != "192.168.2.10"
            or "gateway" in route[0]
        ):
            raise RuntimeError("board_route_mismatch")

    @staticmethod
    def is_our_address(addresses: list[dict[str, Any]]) -> bool:
        return len(addresses) == 1 and (
            addresses[0].get("local"),
            addresses[0].get("prefixlen"),
        ) == ("192.168.2.10", 24)

    def restore(self) -> None:
        if not self.owned:
            return
        rows = ip("address", "show", "dev", self.interface)
        if len(rows) != 1 or any(rows[0].get(k) != v for k, v in self.identity.items()):
            raise RuntimeError("host_restore_unverified: interface changed; route preserved")
        addresses = [a for a in rows[0].get("addr_info", []) if a.get("family") == "inet"]
        if addresses and not self.is_our_address(addresses):
            raise RuntimeError("host_restore_unverified: addresses changed; route preserved")
        if addresses:
            ip("address", "del", ADDRESS, "dev", self.interface)
        ip("link", "set", "dev", self.interface, "down")
        rows = ip("address", "show", "dev", self.interface)
        if any(a.get("family") == "inet" for a in rows[0].get("addr_info", [])) or "UP" in rows[
            0
        ].get("flags", []):
            raise RuntimeError("host_restore_unverified")
        self.state.unlink()
        self.owned = False


def cleanup_network(lock: DeviceLock, network: Network, timeout: float = 650) -> None:
    deadline = time.monotonic() + timeout
    while lock.fd is None:
        try:
            lock.acquire(recovery=True)
        except DeviceBusy:
            if time.monotonic() >= deadline:
                raise RuntimeError("host_cleanup_blocked_by_rx: route preserved") from None
            time.sleep(1)
    try:
        if network.owned and (
            (lock.root / "recovery-required.json").exists() or lock.pending_jobs()
        ):
            raise RuntimeError("rx_restore_unverified: route preserved for recovery")
        network.restore()
    finally:
        lock.release()


def run() -> None:
    os.umask(0o077)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    host = Path("/host-state")
    host.mkdir(exist_ok=True, mode=0o700)
    # This bind mount is host-global even across Compose projects and custom
    # device directories. Do not unlink a flock file while another helper exists.
    with (host / "helper.lock").open("a") as exclusive:
        try:
            fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "host_preparation_in_use: another Compose receiver is running"
            ) from None
        ready = Path("/run/pcscd/host-ready")
        ready.unlink(missing_ok=True)
        host_pcsc = Path("/host-pcsc/pcscd.comm")
        if host_pcsc.exists() and not host_pcsc.is_socket():
            raise RuntimeError("host_pcsc_not_socket: existing host path was preserved")
        uid, gid = int(os.environ.get("SDR_UID", "10001")), int(os.environ.get("SDR_GID", "10001"))
        if uid <= 0 or gid <= 0:
            raise RuntimeError("non_root_app_user_required")
        for folder in (Path("/device"), Path("/data")):
            initialize_directory(folder, uid, gid)
        existing_lock = Path("/device/.device.lock")
        if existing_lock.exists() and existing_lock.stat().st_uid != uid:
            raise RuntimeError("device_lock_owner_mismatch: existing lock was preserved")
        lock = DeviceLock(Path("/device"))
        # Existing unresolved RX evidence must remain usable for WebUI recovery.
        lock.acquire(recovery=True)
        assert lock.fd is not None
        os.fchown(lock.fd, uid, gid)
        network = Network(host / "network.json")
        daemon: subprocess.Popen[bytes] | None = None
        try:
            network.prepare()
            socket_path = Path("/run/pcscd/pcscd.comm")
            socket_path.unlink(missing_ok=True)
            if host_pcsc.is_socket():
                with socket.socket(socket.AF_UNIX) as probe:
                    probe.settimeout(2)
                    probe.connect(str(host_pcsc))
            # A root-owned, fixed UNIX relay reuses the installed PC/SC daemon.
            # Only this Compose project's nonroot app can reach its 0600 socket.
            # No host service/policy changes and no second owner of the USB reader.
            command = (
                [
                    "socat",
                    "UNIX-LISTEN:/run/pcscd/pcscd.comm,fork,unlink-early,mode=0600",
                    "UNIX-CONNECT:/host-pcsc/pcscd.comm",
                ]
                if host_pcsc.is_socket()
                else [
                    "pcscd",
                    "--foreground",
                    "--disable-polkit",
                    "--reader-name-no-serial",
                    "--reader-name-no-interface",
                ]
            )
            daemon = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(50):
                if daemon.poll() is not None:
                    raise RuntimeError("pcsc_start_failed: check exclusive card reader access")
                if socket_path.is_socket():
                    break
                if stop.wait(0.1):
                    return
            else:
                raise RuntimeError("pcsc_start_timeout")
            os.chown(socket_path, uid, gid)
            socket_path.chmod(0o600)
            ready.write_text("ready\n")
            ready.chmod(0o644)
            lock.release()
            print("SDR connection prepared; reception starts only from the WebUI.", flush=True)
            while not stop.wait(1):
                if daemon.poll() is not None:
                    raise RuntimeError("pcsc_ended: stop reception and inspect the card reader")
        finally:
            ready.unlink(missing_ok=True)
            if daemon is not None:
                try:
                    os.killpg(daemon.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(daemon.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    daemon.wait()
            # Compose stops the API first. Also protect workers left alive by
            # abrupt API termination; never remove connectivity under them.
            cleanup_network(lock, network)
            print("Temporary SDR network settings restored; device lock released.", flush=True)


if __name__ == "__main__":
    try:
        run()
    except (RuntimeError, DeviceBusy, OSError, ValueError, subprocess.SubprocessError) as error:
        # No subprocess stderr, interface identity or private paths in logs.
        print(
            str(error) if isinstance(error, RuntimeError) else type(error).__name__, file=sys.stderr
        )
        sys.exit(1)
