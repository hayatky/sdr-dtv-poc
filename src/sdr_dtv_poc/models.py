# SPDX-License-Identifier: GPL-3.0-or-later
"""Public API models shared by demo and future live adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SESSION_LIMIT = 600
SCAN_LIMIT = 180
START_GRACE = 15
STOP_GRACE = 5
RECORDING_LIMIT = 300


class InputKind(StrEnum):
    synthetic = "synthetic"
    saved_ts = "saved_ts"
    live = "live"


class State(StrEnum):
    starting = "starting"
    running = "running"
    stopping = "stopping"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class Restore(StrEnum):
    not_required = "not_required"
    pending = "pending"
    verified = "verified"
    unknown = "unknown"
    failed = "failed"


class EndReason(StrEnum):
    eof = "eof"
    requested = "requested"
    deadline = "deadline"
    startup_timeout = "startup_timeout"
    worker_failed = "worker_failed"
    storage_full = "storage_full"
    database_error = "database_error"
    output_limit = "output_limit"
    server_shutdown = "server_shutdown"
    server_restart = "server_restart"
    source_ended = "source_ended"


class Stage(StrEnum):
    input = "input"
    transport = "transport"
    storage = "storage"
    cleanup = "cleanup"
    complete = "complete"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionStart(StrictModel):
    request_id: UUID
    input_kind: InputKind = InputKind.synthetic
    source_id: str = Field(default="demo", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    duration_seconds: int = Field(default=60, ge=1, le=SESSION_LIMIT)
    service_key: str | None = Field(default=None, max_length=128)
    selected_service_id: int = Field(default=1, ge=1, le=65535)
    enable_hls: bool = True


class Session(BaseModel):
    id: UUID
    request_id: UUID
    input_kind: InputKind
    source_id: str
    state: State
    stage: Stage = Stage.input
    duration_seconds: int
    started_at: str
    deadline_at: str
    ended_at: str | None = None
    end_reason: EndReason | None = None
    error_stage: Stage | None = None
    restore: Restore = Restore.not_required
    partial: bool = False
    bytes_received: int = 0
    artifact_id: UUID | None = None
    service: Service | None = None
    selected_service_id: int = 1
    ts_started_at: str | None = None
    hls: MediaStatus | None = None


class Error(BaseModel):
    code: str
    message: str


class Diagnostic(BaseModel):
    status: str
    code: str


class Diagnostics(BaseModel):
    input_kind: InputKind
    checks: dict[str, Diagnostic]
    starts_receiver: bool = False


class Artifact(BaseModel):
    id: UUID
    session_id: UUID
    kind: str
    media_type: str
    size_bytes: int
    sha256: str
    partial: bool = False
    members: list[str] = Field(default_factory=list)
    playlist_snapshot: str | None = None


# A channel list remains compatible with the stage 1 reserved API.
class ScanStart(StrictModel):
    request_id: UUID
    input_kind: InputKind = InputKind.synthetic
    channels: list[Annotated[int, Field(ge=13, le=52)]] = Field(
        default_factory=lambda: [13, 14], min_length=1, max_length=40
    )

    @model_validator(mode="after")
    def unique_channels(self) -> ScanStart:
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("duplicate channels")
        return self

    duration_seconds: int = Field(default=SCAN_LIMIT, ge=1, le=SCAN_LIMIT)


class RecordingStart(StrictModel):
    request_id: UUID
    session_id: UUID
    duration_seconds: int = Field(default=RECORDING_LIMIT, ge=1, le=RECORDING_LIMIT)


class Service(BaseModel):
    id: str
    name: str | None
    input_kind: InputKind
    physical_channel: int | None = None
    service_id: int
    detection_stage: str
    source_id: str = "demo"
    frequency_hz: int | None = None
    original_network_id: int | None = None
    transport_stream_id: int | None = None
    remote_control_key_id: int | None = None
    detected_at: str | None = None
    profile: dict[str, str | int] = Field(default_factory=dict)
    current_reception: bool = False


class MediaStatus(BaseModel):
    state: str = "starting"
    artifact_id: UUID | None = None
    url: str | None = None
    ready_at: str | None = None
    error_code: str | None = None
    error_stage: str | None = None


class ScanResult(BaseModel):
    physical_channel: int
    frequency_hz: int
    state: str = "not_run"
    service_ids: list[str] = Field(default_factory=list)


class Scan(BaseModel):
    id: UUID
    request_id: UUID
    input_kind: InputKind
    state: State = State.starting
    started_at: str
    deadline_at: str
    ended_at: str | None = None
    end_reason: EndReason | None = None
    restore: Restore = Restore.not_required
    current_channel: int | None = None
    completed_channels: int = 0
    total_channels: int
    elapsed_seconds: float = 0
    results: list[ScanResult]


class Recording(BaseModel):
    id: UUID
    request_id: UUID
    session_id: UUID
    input_kind: InputKind
    service: Service | None = None
    selected_service_id: int = 1
    state: State = State.running
    started_at: str
    deadline_at: str
    duration_seconds: int
    ended_at: str | None = None
    elapsed_seconds: float = 0
    bytes_written: int = 0
    source_offset_bytes: int = 0
    partial: bool = False
    end_reason: EndReason | None = None
    artifact_id: UUID | None = None
    download_url: str | None = None
    file_available: bool = False


class Playback(MediaStatus):
    id: UUID
    recording_id: UUID
    started_at: str
    deadline_at: str
    ended_at: str | None = None


Session.model_rebuild()
