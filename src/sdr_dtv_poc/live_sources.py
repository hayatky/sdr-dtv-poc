# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin separately acquired live sources; never copy them into the product."""

import hashlib
from pathlib import Path

COMMIT = "729400f8e2c42ad30f1b5496a22db457d19c839b"
EXPECTED = {
    "pocs/isdb-t-ts/local_cp.py": "9ba4a07e88353f2f77828804df10c8a3654e026a59b47bd848b7368e315dde4b",
    "src/receiver/__init__.py": "24a7c64cca8e87edfcccc0914b3fbdd168cedca8173de4c0ae3bd201aeeb3962",
    "src/receiver/worker.py": "87f9824133af9193f947c14dcb88ef6d4ab88934bbc3dd58734d5eba3d6a03c6",
    "pocs/iq-lab/rx_capture.py": "16ae895ef6b920ebe8e720da08a4a841faf81f7521feeda195417c3a80113b96",
    "scripts/iio-readonly-probe.py": "eecd262773f6292916012415de08558696f14355f16d673bbfbe65ed30356b7f",
    "pocs/isdb-t-ts/tmcc.py": "88f21beac0b9183e85eb9f143cf5cd1192fc166137e8f36d2d70e1fd66644799",
    "pocs/isdb-t-ts/wideband/file_receiver.py": "362e5c96bb37911ee657b21e9e43b27b63d169506559fbfe4dd42163dfb6fe80",
    "pocs/isdb-t-ts/wideband/fec_evidence.py": "664e5875852f8023f13b9d6fe5e79d38dc2f7b92100fe8018752ec3c3c4e7113",
    "pocs/isdb-t-ts/wideband/live_multiplex.py": "47c38ac26ff7fad3bedaf841e00bb48f9ad1df5231698f84b6d33904b7b75c73",
    "pocs/isdb-t-ts/wideband/multiplex_model.py": "30f86e7ccc4e47ef5e9d5f0bd74c1d48d25317d3f7bd1ca0bdfbdcab4ad595cc",
    "pocs/isdb-t-ts/wideband/reconstruct_multiplex.py": "b0cc2ce16cb998dcaab7a6747fa69a63f58e39372baf17d6d86e9a7385613dc1",
    "pocs/isdb-t-ts/wideband/stream_convert_ci16.py": "328b490ba8f0c47e1000839b0dcb44fd5a90a0071556e039c106d0e4d83ee1bf",
}


def verify(root: Path) -> None:
    for name, expected in EXPECTED.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("research live source hash mismatch")
