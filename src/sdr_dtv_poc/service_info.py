# SPDX-License-Identifier: GPL-3.0-or-later
"""Read actual SDT using TSDuck's CRC/continuity checks and ARIB decoder.

The input is a separate, bounded layer-A TS from the same scan acquisition.
It is never concatenated with the viewing/recording TS.
"""

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_names(xml: bytes, tsid: int | None) -> dict[int, tuple[int, str]]:
    root = ET.fromstring(xml)
    if tsid is None:
        return {}
    result: dict[int, tuple[int, str]] = {}
    conflicts: set[int] = set()
    for table in root.findall("SDT"):
        if table.get("actual") != "true" or table.get("current") != "true":
            continue
        if int(table.attrib["transport_stream_id"], 0) != tsid:
            continue
        onid = int(table.attrib["original_network_id"], 0)
        for service in table.findall("service"):
            descriptor = service.find("service_descriptor")
            if descriptor is None:
                continue
            sid = int(service.attrib["service_id"], 0)
            name = descriptor.get("service_name", "").strip()
            if not name or len(name) > 256 or "\ufffd" in name:
                continue
            if any(ord(c) < 32 or 0x7F <= ord(c) < 0xA0 for c in name):
                continue
            item = (onid, name)
            if sid in result and result[sid] != item:
                conflicts.add(sid)
            result[sid] = item
    return {sid: item for sid, item in result.items() if sid not in conflicts}


async def read_tables(path: Path, pid: int, tid: int) -> bytes:
    if not path.is_file() or path.stat().st_size == 0:
        return b"<tsduck/>"
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("service information input exceeds limit")
    # Default tstables behavior discards invalid CRCs, incomplete tables and
    # next tables. Do not enable pack-and-flush or invalid-sections.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "sdr_dtv_poc.child_exec",
        str(os.getpid()),
        "tstables",
        "--japan",
        "--pid",
        str(pid),
        "--tid",
        str(tid),
        "--xml-output",
        "-",
        str(path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            assert process.stdout
            try:
                xml = await process.stdout.readexactly(1_048_577)
            except asyncio.IncompleteReadError as exc:
                xml = exc.partial
            if len(xml) > 1_048_576 or await process.wait() != 0:
                raise RuntimeError("service information decoder failed")
        # tstables emits no XML at all when there is no complete valid table.
        return xml or b"<tsduck/>"
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


async def pat_tsid(path: Path) -> int | None:
    root = ET.fromstring(await read_tables(path, 0, 0))
    ids = {
        int(t.attrib["transport_stream_id"], 0)
        for t in root.findall("PAT")
        if t.get("current") == "true"
    }
    return ids.pop() if len(ids) == 1 else None


async def service_names(path: Path, tsid: int | None) -> dict[int, tuple[int, str]]:
    return parse_names(await read_tables(path, 17, 0x42), tsid)
