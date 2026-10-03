# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite, paced MPEG-TS source. stdout is TS only, stdin EOF also stops the child."""

import argparse
import os
import select
import sys
import time
from pathlib import Path

PACKET = 188
CHUNK = PACKET * 7


def stream(path: Path, bitrate: int) -> None:
    start = time.monotonic()
    total = 0
    with path.open("rb") as source:
        while data := source.read(CHUNK):
            if len(data) % PACKET or any(data[n] != 0x47 for n in range(0, len(data), PACKET)):
                raise ValueError("invalid TS framing")
            # The parent holds stdin open. Its death closes the pipe.
            if select.select([sys.stdin.buffer], [], [], 0)[0]:
                return
            while not select.select([], [sys.stdout.fileno()], [], 0.1)[1]:
                if (
                    select.select([sys.stdin.buffer], [], [], 0)[0]
                    or time.monotonic() - start >= 600
                ):
                    return
            if time.monotonic() - start >= 600:
                return
            os.write(sys.stdout.fileno(), data)
            total += len(data)
            delay = start + total * 8 / bitrate - time.monotonic()
            if delay > 0 and select.select([sys.stdin.buffer], [], [], delay)[0]:
                return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--bitrate", type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.bitrate <= 50_000_000:
        raise SystemExit(2)
    try:
        stream(args.source, args.bitrate)
    except (OSError, ValueError):
        # Do not disclose paths or source bytes through subprocess error logs.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
