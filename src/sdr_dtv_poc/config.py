# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .live_config import LiveConfig


@dataclass(frozen=True)
class Source:
    path: Path
    bitrate: int
    live_id: str | None = None


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("data/app")
    origin: str = "http://localhost:8000"
    demo_path: Path = Path("data/demo/demo.ts")
    demo_bitrate: int = 1_000_000
    saved_sources: dict[str, Source] = field(default_factory=dict)
    max_output_bytes: int = 4_000_000_000
    max_recording_bytes: int = 2_000_000_000
    max_hls_bytes: int = 32 * 1024 * 1024
    max_playback_bytes: int = 256 * 1024 * 1024
    media_queue_chunks: int = 256
    device_lock_dir: Path | None = None
    min_free_bytes: int = 128 * 1024 * 1024
    live: LiveConfig | None = None
    cas_executable: Path | None = None

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
            live=LiveConfig.read(Path(os.environ["SDR_LIVE_CONFIG"]))
            if os.environ.get("SDR_LIVE_CONFIG")
            else None,
            cas_executable=Path(os.environ["SDR_CAS_EXECUTABLE"])
            if os.environ.get("SDR_CAS_EXECUTABLE")
            else None,
            device_lock_dir=Path(os.environ["SDR_DEVICE_LOCK_DIR"])
            if os.environ.get("SDR_DEVICE_LOCK_DIR")
            else None,
        )
