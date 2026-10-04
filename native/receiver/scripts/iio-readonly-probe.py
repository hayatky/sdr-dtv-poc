# SPDX-License-Identifier: GPL-3.0-or-later
#!/usr/bin/env python3
"""Inspect an IIOD context using only PRINT, selected RX READ, and EXIT.

Output contains device/channel/attribute names and selected RX attribute
values. Inspect the output before committing it; the script never records
arbitrary configuration or per-device identifiers from attribute values.
"""

import argparse
import ipaddress
import json
import socket
import sys
import xml.etree.ElementTree as ET


MAX_XML_BYTES = 2_000_000
MAX_ATTR_BYTES = 16_384
READ_ATTRS = frozenset(
    {
        "frequency",
        "gain_control_mode",
        "hardwaregain",
        "rf_bandwidth",
        "sampling_frequency",
    }
)


def read_exact(stream, count):
    chunks = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("IIOD closed the connection during a response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def response_length(stream, maximum):
    line = stream.readline(32)
    if not line.endswith(b"\n"):
        raise ValueError("invalid IIOD response header")
    length = int(line.strip())
    if length > maximum:
        raise ValueError("IIOD response exceeds the size limit")
    return length


def send_command(sock, command):
    sock.sendall(command.encode("ascii") + b"\r\n")


def fetch_payload(sock, stream, command, maximum):
    send_command(sock, command)
    length = response_length(stream, maximum)
    if length < 0:
        return length, None
    payload = read_exact(stream, length + 1)
    if not payload.endswith(b"\n"):
        raise ValueError("invalid IIOD response terminator")
    return length, payload[:-1]


def safe_token(value):
    if not value or any(char not in "-_.:0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" for char in value):
        raise ValueError("unexpected device, channel, or attribute name")
    return value


def recorded_rx_value(value):
    try:
        decoded = value.decode("utf-8").strip(" \t\r\n\x00")
    except UnicodeDecodeError:
        return {"status": "value_unrecorded", "byte_length": len(value)}
    if not decoded:
        return {"status": "empty"}
    if len(decoded) > 128 or not decoded.isprintable():
        return {"status": "value_unrecorded", "byte_length": len(value)}
    return {"status": "ok", "value": decoded}


def inspect(sock):
    stream = sock.makefile("rb")
    try:
        length, payload = fetch_payload(sock, stream, "PRINT", MAX_XML_BYTES)
        if length < 0:
            raise RuntimeError(f"IIOD PRINT failed: {length}")
        root = ET.fromstring(payload)
        if root.tag != "context":
            raise ValueError("IIOD did not return an IIO context")
        result = {"devices": []}
        for device in root.findall("device"):
            device_id = safe_token(device.get("id"))
            entry = {
                "id": device_id,
                "name": device.get("name"),
                "attributes": sorted(attr.get("name") for attr in device.findall("attribute") if attr.get("name")),
                "channels": [],
            }
            for channel in device.findall("channel"):
                channel_id = safe_token(channel.get("id"))
                direction = channel.get("type")
                if direction not in ("input", "output"):
                    raise ValueError("unexpected IIO channel direction")
                attrs = sorted(attr.get("name") for attr in channel.findall("attribute") if attr.get("name"))
                channel_entry = {
                    "id": channel_id,
                    "name": channel.get("name"),
                    "direction": direction,
                    "attributes": attrs,
                    "read_status": {},
                }
                # Metadata only: no additional hardware attribute READ.
                if entry["name"] == "cf-ad9361-lpc" and direction == "input":
                    scan = channel.find("scan-element")
                    if scan is not None:
                        channel_entry["scan_element"] = {
                            key: scan.get(key) for key in ("index", "format", "scale")
                            if scan.get(key) is not None
                        }
                # Output is the IIO direction, not an RF TX classification:
                # RX_LO is an output channel of the PHY. Never READ a TX or
                # DDS channel, even though reading should not enable them.
                is_rx = entry["name"] == "ad9361-phy" and (
                    direction == "input" or channel.get("name") == "RX_LO"
                )
                for attr in attrs:
                    if not is_rx or attr not in READ_ATTRS:
                        continue
                    command = f"READ {device_id} {direction.upper()} {channel_id} {safe_token(attr)}"
                    count, value = fetch_payload(sock, stream, command, MAX_ATTR_BYTES)
                    if count < 0:
                        channel_entry["read_status"][attr] = {"status": f"error {count}"}
                        continue
                    channel_entry["read_status"][attr] = recorded_rx_value(value)
                entry["channels"].append(channel_entry)
            result["devices"].append(entry)
        send_command(sock, "EXIT")
        return result
    finally:
        stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="numeric board IPv4 address from this board's config")
    parser.add_argument("--port", type=int, default=30431, help="IIOD port; default: 30431")
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.host)
    if not address.is_private or any(
        (address.is_loopback, address.is_link_local, address.is_unspecified, address.is_multicast, address.is_reserved)
    ):
        parser.error("board address must be a private non-local IPv4 address")
    if not 1 <= args.port <= 65535:
        parser.error("port out of range")
    try:
        with socket.create_connection((str(address), args.port), timeout=5) as sock:
            sock.settimeout(5)
            result = inspect(sock)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    except (OSError, EOFError, ValueError, ET.ParseError, RuntimeError) as error:
        print(f"IIO read-only probe failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
