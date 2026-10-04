# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated measured profiles and optional administrator scan presets."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields
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
    segments_a: int = 1
    segments_b: int = 12
    layer: str = "b"
    modulation_a: int = 4
    rate_a: int = 1
    partial_reception: bool = True

    def __post_init__(self) -> None:
        if not (
            13 <= self.channel <= 52
            and self.mode in (1, 2, 3)
            and self.gi in (0.25, 0.125, 0.0625, 0.03125)
            and self.rate_b in range(5)
            and self.interleave_a
            in (
                0,
                4 // (2 ** (self.mode - 1)),
                8 // (2 ** (self.mode - 1)),
                16 // (2 ** (self.mode - 1)),
            )
            and self.interleave_b
            in (
                0,
                4 // (2 ** (self.mode - 1)),
                8 // (2 ** (self.mode - 1)),
                16 // (2 ** (self.mode - 1)),
            )
            and (
                (
                    self.layer == "a"
                    and self.segments_a == 13
                    and self.segments_b == 0
                    and self.modulation_a in (4, 16, 64)
                    and self.rate_a in range(5)
                )
                or (
                    self.layer == "b"
                    and 1 <= self.segments_a <= 12
                    and self.modulation_a == 4
                    and self.rate_a == 1
                )
            )
            and self.segments_a + self.segments_b == 13
            and 0 <= self.gain_db <= 30
        ):
            raise ValueError("unsupported live profile")

    @classmethod
    def from_service(cls, value: Mapping[str, object]) -> "LiveProfile":
        data = {f.name: value[f.name] for f in fields(cls) if f.name in value}
        if "layer" in data:
            data["layer"] = str(data["layer"]).lower()
        return cls(**data)  # type: ignore[arg-type]


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
        if len(profiles) > 40:
            raise ValueError("invalid live profiles")
        verify(Path(data["research_root"]))
        return cls(Path(data["research_root"]), Path(data["native_python"]), profiles, path)
