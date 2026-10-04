# SPDX-License-Identifier: GPL-3.0-or-later
"""No devices or host settings are accessed by these tests."""

import json
from pathlib import Path
from typing import Any

import pytest

from sdr_dtv_poc import host_service as host
from sdr_dtv_poc.device_lock import DeviceLock


class FakeIP:
    def __init__(self) -> None:
        self.row: dict[str, Any] = {
            "ifindex": 7,
            "ifname": "test0",
            "address": "02:00:00:00:00:01",
            "addr_info": [],
            "flags": [],
        }
        self.calls: list[tuple[str, ...]] = []
        self.route: dict[str, Any] = {"dev": "test0", "prefsrc": "192.168.2.10"}

    def __call__(self, *args: str) -> list[dict[str, Any]]:
        self.calls.append(args)
        if args[:2] == ("address", "show"):
            return [self.row]
        if args[:2] == ("route", "show"):
            return []
        if args[:2] == ("route", "get"):
            return [self.route]
        if args[:2] == ("address", "add"):
            self.row["addr_info"] = [{"family": "inet", "local": "192.168.2.10", "prefixlen": 24}]
        if args[:2] == ("address", "del"):
            self.row["addr_info"] = []
        if args[:2] == ("link", "set"):
            self.row["flags"] = ["UP"] if args[-1] == "up" else []
        return []


@pytest.fixture
def net(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[host.Network, FakeIP]:
    fake = FakeIP()
    monkeypatch.setattr(host, "ip", fake)
    monkeypatch.setattr(host, "find_interface", lambda: "test0")
    return host.Network(tmp_path / "network.json"), fake


def test_prepare_restore_and_restart_owned_connection(net: tuple[host.Network, FakeIP]) -> None:
    network, fake = net
    network.prepare()
    assert fake.row["addr_info"] and "UP" in fake.row["flags"]
    assert network.state.exists()
    restarted = host.Network(network.state)
    restarted.prepare()
    assert sum(c[:2] == ("address", "add") for c in fake.calls) == 1
    restarted.restore()
    assert not network.state.exists()
    assert fake.row["addr_info"] == fake.row["flags"] == []


@pytest.mark.parametrize("existing", ["link", "address", "stale_identity"])
def test_preserve_unowned_configuration(net: tuple[host.Network, FakeIP], existing: str) -> None:
    network, fake = net
    if existing == "link":
        fake.row["flags"] = ["UP"]
    elif existing == "address":
        fake.row["addr_info"] = [{"family": "inet", "local": "192.0.2.10", "prefixlen": 24}]
    else:
        network.state.write_text(json.dumps({"ifname": "old0"}))
    with pytest.raises(RuntimeError):
        network.prepare()
    network.restore()
    assert not any(c[1] in {"set", "add", "del"} for c in fake.calls)


@pytest.mark.parametrize("change", ["identity", "address"])
def test_cleanup_does_not_overwrite_external_changes(
    net: tuple[host.Network, FakeIP], change: str
) -> None:
    network, fake = net
    network.prepare()
    if change == "identity":
        fake.row["ifindex"] = 8
    else:
        fake.row["addr_info"].append({"family": "inet", "local": "192.0.2.10", "prefixlen": 24})
    fake.calls.clear()
    with pytest.raises(RuntimeError, match="host_restore_unverified"):
        network.restore()
    assert network.state.exists()
    assert not any(c[1] in {"set", "add", "del"} for c in fake.calls)


def test_route_mismatch_still_restores_owned_changes(net: tuple[host.Network, FakeIP]) -> None:
    network, fake = net
    fake.route["gateway"] = "192.0.2.1"
    with pytest.raises(RuntimeError, match="board_route_mismatch"):
        network.prepare()
    network.restore()
    assert not fake.row["addr_info"] and not network.state.exists()


def test_board_selection_requires_exactly_one(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="expected_one_sdr_board"):
        host.find_interface(tmp_path)
    for name in ("1-1", "1-2"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "idVendor").write_text("0456")
        (folder / "idProduct").write_text("b673")
        (tmp_path / f"{name}:1.0/net/test0").mkdir(parents=True)
        if name == "1-1":
            assert host.find_interface(tmp_path) == "test0"
    with pytest.raises(RuntimeError, match="expected_one_sdr_board"):
        host.find_interface(tmp_path)


def test_existing_directory_owner_is_not_changed(tmp_path: Path) -> None:
    (tmp_path / "evidence").write_text("keep")
    stat = tmp_path.stat()
    with pytest.raises(RuntimeError, match="directory_owner_mismatch"):
        host.initialize_directory(tmp_path, stat.st_uid + 1, stat.st_gid)
    assert (tmp_path / "evidence").read_text() == "keep"
    assert tmp_path.stat().st_uid == stat.st_uid


def test_busy_worker_retains_connectivity(net: tuple[host.Network, FakeIP], tmp_path: Path) -> None:
    network, fake = net
    network.prepare()
    worker = DeviceLock(tmp_path / "device")
    worker.acquire()
    cleanup = DeviceLock(worker.root)
    try:
        with pytest.raises(RuntimeError, match="host_cleanup_blocked_by_rx"):
            host.cleanup_network(cleanup, network, timeout=0)
        assert network.state.exists() and fake.row["addr_info"]
    finally:
        worker.release()
    host.cleanup_network(cleanup, network)
    assert not network.state.exists()


@pytest.mark.parametrize("evidence", ["marker", "pending_job"])
def test_unverified_rx_preserves_connection_for_recovery(
    net: tuple[host.Network, FakeIP], tmp_path: Path, evidence: str
) -> None:
    network, fake = net
    network.prepare()
    lock = DeviceLock(tmp_path / "device")
    lock.acquire()
    if evidence == "marker":
        (lock.root / "recovery-required.json").write_text("{}")
    else:
        folder = lock.root / "capture"
        folder.mkdir()
        (folder / "job.json").write_text(
            json.dumps({"state": "failed", "restoration": {"state": "unknown"}})
        )
    lock.release()
    with pytest.raises(RuntimeError, match="rx_restore_unverified"):
        host.cleanup_network(lock, network)
    assert network.state.exists() and fake.row["addr_info"]
    assert lock.fd is None
