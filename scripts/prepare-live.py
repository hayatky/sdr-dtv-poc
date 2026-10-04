# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract pinned local RX sources without modifying a checkout or accessing RF."""

import argparse
import subprocess
from pathlib import Path

from sdr_dtv_poc.live_sources import COMMIT, EXPECTED, verify


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new output directory")
    contents = {
        name: subprocess.run(
            ["git", "-C", str(args.source), "show", f"{COMMIT}:{name}"],
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout
        for name in EXPECTED
    }
    args.output.mkdir(parents=True)
    for name, raw in contents.items():
        path = args.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    verify(args.output)
    print("Pinned live sources prepared; no device operations performed.")


if __name__ == "__main__":
    main()
