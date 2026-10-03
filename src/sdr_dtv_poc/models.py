# SPDX-License-Identifier: GPL-3.0-or-later
"""Public API models shared by demo and future live adapters."""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


# Reserved operations return 501 until their owning issues implement them.
class ScanStart(StrictModel):
    request_id: UUID
    channels: list[Annotated[int, Field(ge=13, le=52)]] = Field(min_length=1, max_length=40)
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
