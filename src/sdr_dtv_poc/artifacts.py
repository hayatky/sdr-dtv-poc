# SPDX-License-Identifier: GPL-3.0-or-later
"""Open registered files without following symlinks, including directory races."""

import os
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

NAME = re.compile(r"[a-zA-Z0-9_-]+\.(?:m3u8|ts|m4s|mp4)\Z")


def remove_owned_directory(root: Path, category: str, owner: UUID) -> None:
    """Delete only an ID-owned directory, never follow a replaced parent link."""
    assert category in {"recordings", "playback"}
    assert shutil.rmtree.avoids_symlink_attacks
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            parent = os.open(category, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return
        try:
            try:
                shutil.rmtree(str(owner), dir_fd=parent)
            except FileNotFoundError:
                pass
        finally:
            os.close(parent)
    finally:
        os.close(fd)


def open_registered(root: Path, relative: str) -> BinaryIO:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid artifact")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    import stat

    if not stat.S_ISREG(os.fstat(result).st_mode):
        os.close(result)
        raise ValueError("not a regular file")
    return os.fdopen(result, "rb")


def chunks(file: BinaryIO) -> Iterator[bytes]:
    try:
        while data := file.read(64 * 1024):
            yield data
    finally:
        file.close()


def validate_playlist(data: bytes, members: list[str]) -> None:
    if len(data) > 64 * 1024:
        raise ValueError("playlist too large")
    text = data.decode("utf-8")
    if not text.startswith("#EXTM3U\n"):
        raise ValueError("invalid playlist")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            # Stage 1 supports plain MPEG-TS media playlists, without URI attributes.
            if "URI=" in line:
                raise ValueError("URI attributes not supported")
            continue
        if not NAME.fullmatch(line) or line not in members:
            raise ValueError("unregistered playlist reference")
