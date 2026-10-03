# SPDX-License-Identifier: GPL-3.0-or-later
"""Administrator-only profiles, never command lines supplied by HTTP clients."""

import json
from dataclasses import dataclass
from pathlib import Path

from .live_sources import verify


@dataclass(frozen=True)
class LiveProfile:
    channel: int
    mode: int
    gi: float
    rate_b: int
    interleave_a: int
    interleave_b: int
    gain_db: int = 20

    def __post_init__(self) -> None:
        if not (
            13 <= self.channel <= 52
            and self.mode == 3
            and self.gi in (0.25, 0.125, 0.0625, 0.03125)
            and self.rate_b in range(5)
            and self.interleave_a in (0, 1, 2, 4)
            and self.interleave_b in (0, 1, 2, 4)
            and 0 <= self.gain_db <= 30
        ):
            raise ValueError("unsupported live profile")


@dataclass(frozen=True)
class LiveConfig:
    research_root: Path
    native_python: Path
    profiles: dict[str, LiveProfile]
    config_path: Path

    @classmethod
    def read(cls, path: Path) -> "LiveConfig":
        data = json.loads(path.read_text())
        if set(data) != {"research_root", "native_python", "profiles"}:
            raise ValueError("unexpected live settings")
        profiles = {key: LiveProfile(**value) for key, value in data["profiles"].items()}
        if not profiles or len(profiles) > 40:
            raise ValueError("invalid live profiles")
        verify(Path(data["research_root"]))
        return cls(Path(data["research_root"]), Path(data["native_python"]), profiles, path)
