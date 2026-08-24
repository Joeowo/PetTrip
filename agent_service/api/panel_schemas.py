"""Agent 面板 Snapshot 的严格公开 DTO。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PanelSnapshotError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool


class PanelSnapshotRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class PanelSnapshotIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination_id: str
    spec_id: str
    spec_version: int = Field(gt=0)
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PanelSnapshotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    run_id: str
    role: str
    content_text: str | None = None
    structured_data: Any | None = None
    choices: list[Any] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None


class PanelSnapshotArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    scene_artifact_id: str
    render_file_id: str
    render_mime_type: str
    render_width_px: int = Field(gt=0)
    render_height_px: int = Field(gt=0)
    render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    download_url: str


class PanelSnapshotCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_state: Literal["unknown", "partial", "ready", "failed"]
    quality_state: Literal["not_evaluated"]
    publish_eligible: bool
    terminal_outcome: str | None = None


class PanelSnapshotProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    panel_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_degraded: bool = False
    projection_lag: int | None = None


class PanelSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["agent_panel_snapshot"] = "agent_panel_snapshot"
    schema_version: Literal["0.1"] = "0.1"
    run: PanelSnapshotRun
    conversation_id: str
    messages: list[PanelSnapshotMessage] = Field(default_factory=list)
    snapshot_identity: PanelSnapshotIdentity
    scene_plans: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[PanelSnapshotArtifact] = Field(default_factory=list)
    completion: PanelSnapshotCompletion
    projection: PanelSnapshotProjection
    request_id: str
