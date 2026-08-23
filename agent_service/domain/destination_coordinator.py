"""目的地协调器 - 跨阶段调度与启动恢复（Issue #16 T4）。

职责：
1. 根据 Repository 已提交里程碑调度阶段
2. 启动恢复：扫描非终态 Destination，决定继续哪个阶段
3. 串行生成两个场景（按 ScenePlan.order）

不包含：
- LangGraph 工作流本身（留给 T3, T5, T7）
- 完整并发场景生成（首阶段串行）
- 复杂的 checkpoint 管理（基础版即可）
"""

from __future__ import annotations

import logging
from typing import Any

from ..storage.destination_storage import DestinationRepository

LOGGER = logging.getLogger("uvicorn.error")


class DestinationCoordinatorService:
    """目的地协调器 - 跨阶段调度。

    普通 Python 服务，不创建 LangGraph 父图。
    根据 Business Repository 的已提交里程碑调度阶段。
    """

    def __init__(self, repository: DestinationRepository) -> None:
        """初始化协调器。

        Args:
            repository: 目的地数据仓库
        """
        self.repository = repository

    def recover_pending_destinations(self) -> dict[str, int]:
        """启动恢复：扫描非终态 Destination，决定继续哪个阶段。

        恢复规则：
        1. 扫描非终态 Destination（done=0）
        2. 读取 Repository 里程碑，决定继续哪个阶段
        3. 已提交的 Requirements、Spec、Artifacts 不重做
        4. 清理未被引用的临时文件

        Returns:
            dict: 恢复统计信息
                - recovered_destinations: 恢复的目的地数量
                - skipped_done: 跳过的已完成目的地数量
        """
        if not self.repository._is_open:
            self.repository.open()

        counts = {
            "recovered_destinations": 0,
            "skipped_done": 0,
        }

        # 查询所有非终态 Destination
        pending_destinations = self._list_pending_destinations()

        for dest in pending_destinations:
            destination_id = dest["id"]
            phase = dest["phase"]
            done = bool(dest["done"])

            if done:
                counts["skipped_done"] += 1
                LOGGER.info(
                    "destination_already_done destination_id=%s phase=%s",
                    destination_id,
                    phase,
                )
                continue

            # 根据当前阶段和 Repository 里程碑决定继续点
            resume_phase = self._determine_resume_phase(destination_id, phase)

            if resume_phase:
                LOGGER.info(
                    "destination_recovery_scheduled destination_id=%s current_phase=%s resume_phase=%s",
                    destination_id,
                    phase,
                    resume_phase,
                )
                counts["recovered_destinations"] += 1
                # TODO: 实际调度工作流（T3, T5, T7 实现）
            else:
                LOGGER.warning(
                    "destination_cannot_resume destination_id=%s phase=%s",
                    destination_id,
                    phase,
                )

        return counts

    def _list_pending_destinations(self) -> list[dict[str, Any]]:
        """列出所有非终态 Destination。

        Returns:
            list[dict]: Destination 记录列表
        """
        # 直接查询数据库获取所有 Destination
        # 这里使用底层连接，因为 DestinationRepository 还没有提供列表方法
        with self.repository._lock:
            assert self.repository._conn is not None
            rows = self.repository._conn.execute(
                "SELECT * FROM destinations ORDER BY created_at ASC"
            ).fetchall()

        return [dict(row) for row in rows]

    def _determine_resume_phase(
        self, destination_id: str, current_phase: str
    ) -> str | None:
        """根据 Repository 里程碑决定从哪个阶段继续。

        Repository 优先原则：
        1. 读取已提交的 Requirements、Spec、Artifacts
        2. 已提交的对象不重做
        3. checkpoint 落后：继续；checkpoint 领先：fail closed

        Args:
            destination_id: 目的地 ID
            current_phase: 当前阶段

        Returns:
            str | None: 应该继续的阶段，None 表示无法恢复
        """
        # 检查 Requirements 是否已冻结
        has_requirements = self._has_frozen_requirements(destination_id)

        # 检查 Spec 是否已锁定
        has_spec = self._has_locked_spec(destination_id)

        # 检查 SharedEnvironment 是否已生成
        has_shared_env = self._has_shared_environment(destination_id)

        # 检查 Scene 状态
        scene_status = self._get_scene_status(destination_id)

        # 根据已提交里程碑决定继续阶段
        if current_phase == "clarification":
            # 澄清阶段：检查是否已关闭
            return "clarification" if not has_requirements else "requirements"

        elif current_phase == "requirements":
            # 要求阶段：如果 Requirements 已冻结，进入规格阶段
            return "specification" if has_requirements else "requirements"

        elif current_phase == "specification":
            # 规格阶段：如果 Spec 已锁定，进入计划阶段
            return "planning" if has_spec else "specification"

        elif current_phase == "planning":
            # 计划阶段：如果 ScenePlans 已创建，进入共享环境生成
            return "shared_environment" if has_spec else "planning"

        elif current_phase == "shared_environment":
            # 共享环境阶段：如果环境已生成，进入场景生成
            return "scene_generation" if has_shared_env else "shared_environment"

        elif current_phase == "scene_generation":
            # 场景生成阶段：检查哪些场景需要继续
            if scene_status["all_ready"]:
                return "terminal"
            elif scene_status["all_failed"]:
                return "terminal"
            else:
                return "scene_generation"

        elif current_phase == "terminal":
            # 终态：不恢复
            return None

        return None

    def _has_frozen_requirements(self, destination_id: str) -> bool:
        """检查 Requirements 是否已冻结。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否已冻结
        """
        with self.repository._lock:
            assert self.repository._conn is not None
            row = self.repository._conn.execute(
                "SELECT 1 FROM destination_requirements WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def _has_locked_spec(self, destination_id: str) -> bool:
        """检查 Spec 是否已锁定。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否已锁定
        """
        with self.repository._lock:
            assert self.repository._conn is not None
            row = self.repository._conn.execute(
                "SELECT 1 FROM destination_specs WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def _has_shared_environment(self, destination_id: str) -> bool:
        """检查 SharedEnvironment 是否已生成。

        Args:
            destination_id: 目的地 ID

        Returns:
            bool: 是否已生成
        """
        with self.repository._lock:
            assert self.repository._conn is not None
            row = self.repository._conn.execute(
                "SELECT 1 FROM shared_environment_artifacts WHERE destination_id = ? LIMIT 1",
                (destination_id,),
            ).fetchone()

        return row is not None

    def _get_scene_status(self, destination_id: str) -> dict[str, Any]:
        """获取场景状态。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict: 场景状态
                - total_scenes: 总场景数
                - ready_scenes: 已完成场景数
                - failed_scenes: 失败场景数
                - all_ready: 是否全部完成
                - all_failed: 是否全部失败
        """
        with self.repository._lock:
            assert self.repository._conn is not None
            # 获取所有场景计划
            scene_plans = self.repository._conn.execute(
                "SELECT scene_id FROM scene_plans WHERE destination_id = ? ORDER BY order_index ASC",
                (destination_id,),
            ).fetchall()

            total_scenes = len(scene_plans)
            ready_scenes = 0
            failed_scenes = 0

            # 检查每个场景的 artifact 状态
            for plan in scene_plans:
                scene_id = plan["scene_id"]
                artifact = self.repository._conn.execute(
                    "SELECT 1 FROM scene_artifacts WHERE scene_id = ? LIMIT 1",
                    (scene_id,),
                ).fetchone()

                if artifact is not None:
                    ready_scenes += 1
                else:
                    # 检查是否有失败的 attempt 记录
                    failed_attempt = self.repository._conn.execute(
                        "SELECT 1 FROM operation_attempts "
                        "WHERE scene_id = ? AND status = 'failed' "
                        "AND attempt_number >= 2 "  # 最多 3 次尝试（0, 1, 2）
                        "LIMIT 1",
                        (scene_id,),
                    ).fetchone()

                    if failed_attempt is not None:
                        failed_scenes += 1

        return {
            "total_scenes": total_scenes,
            "ready_scenes": ready_scenes,
            "failed_scenes": failed_scenes,
            "all_ready": total_scenes > 0 and ready_scenes == total_scenes,
            "all_failed": total_scenes > 0 and failed_scenes == total_scenes,
        }

    def process_destination(self, destination_id: str) -> dict[str, Any]:
        """处理一个目的地的下一个阶段。

        串行生成两个场景（按 ScenePlan.order）。

        Args:
            destination_id: 目的地 ID

        Returns:
            dict: 处理结果
                - phase: 当前阶段
                - status: 处理状态（pending/completed/failed）
                - next_phase: 下一个阶段
        """
        if not self.repository._is_open:
            self.repository.open()

        destination = self.repository.get_destination(destination_id)
        if destination is None:
            raise ValueError(f"目的地不存在: {destination_id}")

        current_phase = destination["phase"]

        # TODO: 根据阶段调度对应的 LangGraph 工作流
        # 这部分将在 T3, T5, T7 中实现
        LOGGER.info(
            "destination_processing destination_id=%s phase=%s",
            destination_id,
            current_phase,
        )

        return {
            "phase": current_phase,
            "status": "pending",
            "next_phase": None,
        }
