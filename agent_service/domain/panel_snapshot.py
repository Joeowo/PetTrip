"""Run-scoped、client-scoped Agent 面板 Snapshot projection。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..api.panel_schemas import (
    PanelSnapshotArtifact,
    PanelSnapshotCompletion,
    PanelSnapshotIdentity,
    PanelSnapshotMessage,
    PanelSnapshotProjection,
    PanelSnapshotResponse,
    PanelSnapshotRun,
)
from ..shared.errors import (
    ApiError,
    RESOURCE_NOT_FOUND,
    SNAPSHOT_INCONSISTENT,
    SNAPSHOT_NOT_READY,
)
from ..domain.snapshot import snapshot_sha256, validate_snapshot_schema, SnapshotSchemaError


@dataclass(frozen=True, slots=True)
class PanelSnapshotResult:
    response: PanelSnapshotResponse
    etag: str


class PanelSnapshotService:
    """面板唯一高层读取 seam；不让 API 直接组合 Repository。"""

    def __init__(self, storage: Any, destination_repository: Any) -> None:
        self.storage = storage
        self.destination_repository = destination_repository

    def get_run_snapshot(
        self, *, run_id: str, api_client_id: str, request_id: str
    ) -> PanelSnapshotResult:
        run = self.storage.get_run(run_id, api_client_id)
        if run is None:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        snapshot = self.destination_repository.get_snapshot_for_run(run_id)
        if snapshot is None:
            raise ApiError(SNAPSHOT_NOT_READY, "Run 尚未形成可读取的 Snapshot。", status=409, retryable=True)
        if snapshot["api_client_id"] != api_client_id:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        try:
            validate_snapshot_schema(snapshot["schema_name"], snapshot["schema_version"])
        except (KeyError, SnapshotSchemaError) as exc:
            raise ApiError(
                "UNSUPPORTED_SCHEMA", "不支持的 Snapshot schema。", status=422
            ) from exc

        destination = self.destination_repository.get_destination(snapshot["destination_id"])
        requirements = self.destination_repository.get_destination_requirements_by_id(
            snapshot["requirements_id"], snapshot["destination_id"]
        )
        spec = self.destination_repository.get_destination_spec_by_id(snapshot["spec_id"])
        plans = self.destination_repository.list_scene_plans_for_spec(snapshot["spec_id"])
        artifacts = self.destination_repository.list_scene_artifacts_for_snapshot(snapshot["snapshot_id"])
        items = (
            self.destination_repository.list_requirement_items(snapshot["requirements_id"])
            if requirements is not None
            else []
        )
        self._verify_consistency(
            snapshot, destination, requirements, items, spec, plans, artifacts, api_client_id
        )

        identity = PanelSnapshotIdentity(
            destination_id=snapshot["destination_id"],
            spec_id=snapshot["spec_id"],
            spec_version=snapshot["spec_version"],
            spec_sha256=snapshot["spec_sha256"],
        )
        artifact_dtos = [
            PanelSnapshotArtifact(
                scene_id=artifact["scene_id"],
                scene_artifact_id=artifact["scene_artifact_id"],
                render_file_id=artifact["render_file_id"],
                render_mime_type=artifact["render_mime_type"],
                render_width_px=artifact["render_width_px"],
                render_height_px=artifact["render_height_px"],
                render_sha256=artifact["render_sha256"],
                download_url=f"/api/v1/files/{artifact['render_file_id']}/content",
            )
            for artifact in artifacts
        ]
        delivery_state = "ready" if len(artifacts) == len(plans) else "partial"
        public_payload = {
            "schema_name": "agent_panel_snapshot",
            "schema_version": "0.1",
            "run_id": run["id"],
            "session_id": run["session_id"],
            "conversation_id": run["session_id"],
            "snapshot_identity": identity.model_dump(mode="json"),
            "scene_plans": [
                {"scene_id": plan["scene_id"], "order_index": plan["order_index"]}
                for plan in plans
            ],
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifact_dtos],
            "completion": {
                "delivery_state": delivery_state,
                "quality_state": "not_evaluated",
                "publish_eligible": bool(
                    delivery_state == "ready"
                    and destination["done"]
                    and destination["terminal_outcome"] == "succeeded"
                ),
                "terminal_outcome": destination["terminal_outcome"],
            },
        }
        revision = snapshot_sha256(public_payload, excluded_fields={"download_url"})
        response = PanelSnapshotResponse(
            run=PanelSnapshotRun(
                run_id=run["id"],
                session_id=run["session_id"],
                status=run["status"],
                created_at=run.get("created_at"),
                started_at=run.get("started_at"),
                completed_at=run.get("completed_at"),
            ),
            conversation_id=run["session_id"],
            snapshot_identity=identity,
            scene_plans=public_payload["scene_plans"],
            artifacts=artifact_dtos,
            completion=PanelSnapshotCompletion(**public_payload["completion"]),
            projection=PanelSnapshotProjection(panel_revision=revision),
            request_id=request_id,
        )
        return PanelSnapshotResult(response=response, etag=f'"{revision}"')

    @staticmethod
    def _verify_consistency(
        snapshot: dict[str, Any],
        destination: dict[str, Any] | None,
        requirements: dict[str, Any] | None,
        items: list[dict[str, Any]],
        spec: dict[str, Any] | None,
        plans: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
        api_client_id: str,
    ) -> None:
        if destination is None or destination["api_client_id"] != api_client_id:
            raise ApiError(RESOURCE_NOT_FOUND, "Run 不存在。", status=404)
        if requirements is None or spec is None:
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot 依赖不一致。", status=409)
        if requirements["requirements_id"] != snapshot["requirements_id"]:
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot Requirements 不一致。", status=409)
        try:
            requirements_data = {
                "source_inputs": json.loads(requirements["source_inputs"]),
                "items": [
                    {
                        "normalized_statement": item["normalized_statement"],
                        "polarity": item["polarity"],
                        "fulfillment": item["fulfillment"],
                        "source_type": item["source_type"],
                        "source_input_ids": json.loads(item["source_input_ids"]),
                        "rationale": item["rationale"],
                    }
                    for item in items
                ],
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot Requirements 数据损坏。", status=409) from exc
        requirements_hash = hashlib.sha256(
            json.dumps(requirements_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if requirements_hash != snapshot["requirements_sha256"]:
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot Requirements hash 不一致。", status=409)
        if spec["spec_id"] != snapshot["spec_id"] or spec["sha256"] != snapshot["spec_sha256"]:
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot Spec hash 不一致。", status=409)
        if len(plans) != 2 or any(plan["destination_id"] != snapshot["destination_id"] for plan in plans):
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot ScenePlan 不一致。", status=409)
        if any(artifact["destination_id"] != snapshot["destination_id"] for artifact in artifacts):
            raise ApiError(SNAPSHOT_INCONSISTENT, "Snapshot artifact 不一致。", status=409)
