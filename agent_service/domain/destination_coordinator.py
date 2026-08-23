"""目的地协调器：按 Repository 里程碑驱动完整生成主链路。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ..storage.destination_storage import DestinationRepository

LOGGER = logging.getLogger("uvicorn.error")

WorkflowRunner = Callable[[dict[str, Any]], dict[str, Any]]


class DestinationCoordinatorService:
    """基于已提交里程碑调度目的地生成阶段。

    协调器只负责阶段选择与调用，不复制领域对象；每次调度前都重新从
    Repository 读取状态，因此重复触发不会重新提交已经存在的制品。
    """

    def __init__(
        self,
        repository: DestinationRepository,
        workflows: dict[str, WorkflowRunner] | None = None,
    ) -> None:
        self.repository = repository
        self.workflows = workflows or {}
        self._dispatch_lock = threading.RLock()

    def recover_pending_destinations(self) -> dict[str, int]:
        """兼容旧的启动恢复入口；不启动后台线程。"""
        if not self.repository._is_open:
            self.repository.open()
        counts = {"recovered_destinations": 0, "skipped_done": 0}
        for destination in self.repository.list_destinations():
            if destination["done"]:
                counts["skipped_done"] += 1
                continue
            if self._determine_resume_phase(destination["id"], destination["phase"]):
                counts["recovered_destinations"] += 1
        return counts

    def dispatch_destination(
        self, destination_id: str, *, run_id: str | None = None
    ) -> dict[str, Any]:
        """连续推进一个目的地当前可执行的阶段。

        每完成一个阶段都重新读取 Repository；遇到未决条件或失败即 fail-closed，
        不猜测恢复语义。返回值是诊断信息，不是公开 Manifest。
        """
        with self._dispatch_lock:
            if not self.repository._is_open:
                self.repository.open()
            destination = self.repository.get_destination(destination_id)
            if destination is None:
                raise ValueError(f"目的地不存在: {destination_id}")

            result: dict[str, Any] = {
                "destination_id": destination_id,
                "status": "pending",
                "phases": [],
                "error": None,
            }
            for _ in range(8):
                destination = self.repository.get_destination(destination_id)
                assert destination is not None
                phase = self._next_executable_phase(destination_id, destination["phase"])
                if phase is None:
                    result["status"] = "completed" if destination["done"] else "pending"
                    return result
                if phase == "terminal":
                    result["status"] = "completed"
                    return result
                runner = self.workflows.get(phase)
                if runner is None:
                    result["error"] = f"workflow_not_configured:{phase}"
                    return result
                try:
                    output = runner({
                        "destination_id": destination_id,
                        "session_id": destination["session_id"],
                        "run_id": run_id,
                    }) or {}
                except Exception as exc:  # noqa: BLE001 - minimal fail-closed boundary
                    LOGGER.exception("destination_phase_failed destination_id=%s phase=%s", destination_id, phase)
                    result["error"] = f"{phase}:{type(exc).__name__}"
                    return result
                result["phases"].append(phase)
                if output.get("error"):
                    result["error"] = output["error"]
                    return result
                if self.repository.get_destination(destination_id) == destination:
                    result["error"] = f"phase_did_not_commit:{phase}"
                    return result
            result["error"] = "phase_progress_limit_exceeded"
            return result

    def process_destination(self, destination_id: str) -> dict[str, Any]:
        """兼容旧入口：记录当前阶段但不强制配置 workflow。"""
        if not self.repository._is_open:
            self.repository.open()
        destination = self.repository.get_destination(destination_id)
        if destination is None:
            raise ValueError(f"目的地不存在: {destination_id}")
        return {
            "phase": destination["phase"],
            "status": "pending",
            "next_phase": self._determine_resume_phase(
                destination_id, destination["phase"]
            ),
        }

    def _next_executable_phase(self, destination_id: str, current_phase: str) -> str | None:
        requirements = self._has_frozen_requirements(destination_id)
        spec = self._has_locked_spec(destination_id)
        shared = self._has_shared_environment(destination_id)
        scenes = self._get_scene_status(destination_id)
        if current_phase == "clarification":
            try:
                clarification = self.repository.get_clarification_state(
                    self.repository.get_destination(destination_id)["session_id"]
                )
            except Exception:
                clarification = None
            if clarification is not None and not clarification["clarification_closed"]:
                return None
            if not requirements or not spec:
                return "clarification"
            return "planning"
        if current_phase == "requirements":
            return "clarification" if not spec else "planning"
        if current_phase == "specification":
            return "planning" if spec else "specification"
        if current_phase == "planning":
            return "shared_environment" if spec and not shared else "scene_generation" if shared else "planning"
        if current_phase == "shared_environment":
            return "scene_generation" if shared else "shared_environment"
        if current_phase == "scene_generation":
            return "terminal" if scenes["all_ready"] or scenes["all_failed"] else "scene_generation"
        return None

    def _determine_resume_phase(self, destination_id: str, current_phase: str) -> str | None:
        """兼容 T4 恢复诊断语义；真正执行由 dispatch 使用下一阶段选择器。"""
        requirements = self._has_frozen_requirements(destination_id)
        spec = self._has_locked_spec(destination_id)
        shared = self._has_shared_environment(destination_id)
        scenes = self._get_scene_status(destination_id)
        if current_phase == "clarification":
            return "requirements" if requirements else "clarification"
        if current_phase == "requirements":
            return "specification" if requirements else "requirements"
        if current_phase == "specification":
            return "planning" if spec else "specification"
        if current_phase == "planning":
            return "shared_environment" if shared else "planning"
        if current_phase == "shared_environment":
            return "scene_generation" if shared else "shared_environment"
        if current_phase == "scene_generation":
            return "terminal" if scenes["all_ready"] or scenes["all_failed"] else "scene_generation"
        return None

    def _has_frozen_requirements(self, destination_id: str) -> bool:
        return self.repository.has_frozen_requirements(destination_id)

    def _has_locked_spec(self, destination_id: str) -> bool:
        return self.repository.has_locked_spec(destination_id)

    def _has_shared_environment(self, destination_id: str) -> bool:
        return self.repository.has_shared_environment(destination_id)

    def _get_scene_status(self, destination_id: str) -> dict[str, Any]:
        return self.repository.get_scene_status(destination_id)
