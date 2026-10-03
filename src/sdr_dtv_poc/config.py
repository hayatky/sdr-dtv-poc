# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Source:
    path: Path
    bitrate: int


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data/app")
    origin: str = "http://localhost:8000"
    demo_path: Path = Path("data/demo/demo.ts")
    demo_bitrate: int = 1_000_000
    saved_sources: dict[str, Source] = field(default_factory=dict)
    max_output_bytes: int = 80_000_000
    min_free_bytes: int = 128 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        sources = {}
        # Local administrator configuration only; never accepted through HTTP.
        config = os.environ.get("SDR_SAVED_SOURCES")
        if config:
            for key, item in json.loads(Path(config).read_text()).items():
                if not 1 <= int(item["bitrate"]) <= 50_000_000:
                    raise ValueError("invalid source bitrate")
                sources[key] = Source(Path(item["path"]), int(item["bitrate"]))
        return cls(
            data_dir=Path(os.environ.get("SDR_DATA_DIR", "data/app")),
            origin=os.environ.get("SDR_ORIGIN", "http://localhost:8000"),
            demo_path=Path(os.environ.get("SDR_DEMO_PATH", "data/demo/demo.ts")),
            saved_sources=sources,
        )
