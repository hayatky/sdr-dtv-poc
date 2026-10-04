# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from sdr_dtv_poc.models import InputKind, Service
from sdr_dtv_poc.scanning import Scans
from sdr_dtv_poc.service_info import parse_names, service_names


def test_names_require_current_actual_matching_transport() -> None:
    def table(**attrs: str) -> str:
        values = dict(
            actual="true", current="true", transport_stream_id="7", original_network_id="8", **{}
        )
        values.update(attrs)
        attributes = " ".join(f'{k}="{v}"' for k, v in values.items())
        return (
            f'<SDT {attributes}><service service_id="9">'
            '<service_descriptor service_name="試験テレビ"/></service></SDT>'
        )

    assert parse_names(f"<tsduck>{table()}</tsduck>".encode(), 7) == {9: (8, "試験テレビ")}
    for changed in ({"actual": "false"}, {"current": "false"}, {"transport_stream_id": "6"}):
        assert not parse_names(f"<tsduck>{table(**changed)}</tsduck>".encode(), 7)
    assert not parse_names(f"<tsduck>{table()}</tsduck>".encode(), None)
    assert not parse_names(
        f"<tsduck>{table()}{table(original_network_id='10')}</tsduck>".encode(), 7
    )


@pytest.mark.parametrize("source_id", ["uhf-18", "renamed-preset"])
def test_new_name_preserves_existing_service_references(source_id: str) -> None:
    old = Service(
        id="existing",
        name=None,
        input_kind=InputKind.live,
        physical_channel=18,
        frequency_hz=503142857,
        service_id=9,
        original_network_id=None,
        transport_stream_id=7,
        source_id="uhf-18",
        detected_at="2026-10-04T00:00:00Z",
        detection_stage="ts_si",
    )
    manager = Mock()
    manager.store.records.return_value = []
    scans = Scans(manager)
    scans.services[old.id] = old
    new = old.model_copy(
        update={"id": "new", "name": "試験テレビ", "original_network_id": 8, "source_id": source_id}
    )
    scans.keep_service_id(new)
    assert new.id == old.id
    scans.services[new.id] = new
    missing = new.model_copy(update={"id": "missing", "name": None, "original_network_id": None})
    scans.keep_service_id(missing)
    assert missing.id == old.id
    assert missing.original_network_id == 8
    assert missing.name is None
    scans.services[missing.id] = missing
    foreign = new.model_copy(update={"id": "foreign", "original_network_id": 10})
    scans.keep_service_id(foreign)
    assert foreign.id == "foreign"

    synthetic = old.model_copy(update={"input_kind": InputKind.synthetic})
    scans.services = {synthetic.id: synthetic}
    other_source = synthetic.model_copy(update={"id": "other", "source_id": "other-file"})
    scans.keep_service_id(other_source)
    assert other_source.id == "other"


def crc(data: bytes) -> bytes:
    value = 0xFFFFFFFF
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
    return value.to_bytes(4)


def sdt_packets() -> bytes:
    # Self-authored name, no captured broadcast bytes. Enough descriptors for
    # two TS packets, so a broken continuity counter invalidates the section.
    name = "試験一".encode("iso2022_jp")[3:-3]
    descriptor = bytes([0x48, len(name) + 3, 1, 0, len(name)]) + name
    descriptor += bytes([0xFE, 170]) + b"x" * 170
    body = bytes.fromhex("0007c100000008ff0009fc")
    body += (0x8000 | len(descriptor)).to_bytes(2) + descriptor
    section = b"\x42" + (0xF000 | (len(body) + 4)).to_bytes(2) + body
    section += crc(section)
    first = b"\x47\x40\x11\x10\0" + section[:183]
    second = (b"\x47\x00\x11\x11" + section[183:]).ljust(188, b"\xff")
    return first + second


@pytest.mark.skipif(shutil.which("tstables") is None, reason="TSDuck is in the live image")
def test_actual_arib_decoder_rejects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.ts"
    good = sdt_packets()
    # Repeat to support input-format detection even for a short synthetic TS.
    path.write_bytes(good * 8)
    assert asyncio.run(service_names(path, 7)) == {9: (8, "試験一")}
    damaged = bytearray(good)
    damaged[40] ^= 1
    path.write_bytes(damaged * 8)
    assert not asyncio.run(service_names(path, 7))
    damaged = bytearray(good)
    damaged[191] = 0x15  # second packet CC no longer follows first packet
    path.write_bytes(damaged * 8)
    assert not asyncio.run(service_names(path, 7))
    path.write_bytes(good[:188] * 8)
    assert not asyncio.run(service_names(path, 7))


def test_service_information_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "too-large.ts"
    with path.open("wb") as file:
        file.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="exceeds limit"):
        asyncio.run(service_names(path, 7))


@pytest.mark.parametrize("cancel", [False, True])
def test_decoder_output_limit_and_cancellation_reap_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    from sdr_dtv_poc.service_info import read_tables

    async def scenario() -> None:
        original = asyncio.create_subprocess_exec
        launched: list[asyncio.subprocess.Process] = []
        started = asyncio.Event()

        async def fake(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            code = (
                "import time; time.sleep(30)"
                if cancel
                else (
                    "import sys,time; sys.stdout.buffer.write(b'x'*1048577); "
                    "sys.stdout.flush(); time.sleep(30)"
                )
            )
            child = await original(sys.executable, "-c", code, stdout=asyncio.subprocess.PIPE)
            launched.append(child)
            started.set()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
        path = tmp_path / "input.ts"
        path.write_bytes(b"x")
        task = asyncio.create_task(read_tables(path, 17, 0x42))
        await started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="decoder failed"):
                await asyncio.wait_for(task, 2)
        assert launched[0].returncode is not None

    asyncio.run(scenario())
