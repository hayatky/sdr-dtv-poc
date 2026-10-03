# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract verified build inputs from a separately acquired research Git checkout.

No device access, network, Docker execution, or research checkout modification.
Outputs stay in ignored data/ by default. Research sources are not redistributed.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

COMMIT = "b1dcbf3688db79f149ff3a255639a36860ec3924"
PREFIX = "pocs/isdb-t-ts/"
EXPECTED = {
    "runtime/Dockerfile": "f0b6c764ea63e1051354c12eb25304bc1f29eac53ce27fb543c40e18cd98d8af",
    "runtime/prepare_source.py": "e501efe12dd85402e4361d3171374d8db42b1f5e004ec1164e5e2ec61ee3e625",
    "wideband/Dockerfile": "1d58ada5d093e2f26817b451bc9118395e40ec5dd955a4dc45fa17a8d7fffc2d",
    "wideband/prepare_source.py": "1d5c8f64f439cdd4fa1aa0f415868a668f187f7c7e301c4d0de4f9f02aba76fe",
}
RUNTIME_FILES = [
    "tmcc.py",
    "wideband/file_receiver.py",
    "wideband/fec_evidence.py",
    "wideband/live_multiplex.py",
    "wideband/multiplex_model.py",
    "wideband/reconstruct_multiplex.py",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="separately acquired research Git checkout")
    parser.add_argument("--output", type=Path, default=Path("data/receiver-build"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new empty location")
    contents: dict[str, bytes] = {}
    for name in [*EXPECTED, *RUNTIME_FILES]:
        raw = subprocess.run(
            ["git", "-C", str(args.source), "show", f"{COMMIT}:{PREFIX}{name}"],
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout
        if name in EXPECTED and hashlib.sha256(raw).hexdigest() != EXPECTED[name]:
            raise RuntimeError("research input hash mismatch")
        contents[name] = raw
    args.output.mkdir(parents=True)
    manifest = {}
    for name, raw in contents.items():
        if name == "wideband/Dockerfile":
            raw = raw.replace(b"FROM hlfec-oneseg:dev", b"FROM sdr-dtv-native-base:issue3")
        destination = args.output / (name if name in EXPECTED else f"files/{name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        manifest[name] = hashlib.sha256(raw).hexdigest()
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "commit": COMMIT,
                "sha256": manifest,
                "changes": ["wideband Dockerfile base tag only"],
            },
            indent=2,
        )
        + "\n"
    )
    print("Fixed receiver build inputs prepared; no device operations performed.")


if __name__ == "__main__":
    main()
