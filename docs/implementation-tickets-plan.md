# Implementation Tickets Plan for Issue #10

基于 issue #10 的实现规格，拆分为以下实现 tickets：

## 拆分原则

1. **垂直切片优先** - 每个 ticket 尽可能包含端到端的一小块功能
2. **依赖关系清晰** - 明确前置依赖，支持并行开发
3. **主链路优先** - 核心流程先通，护栏细节后补
4. **测试内置** - 每个 ticket 包含对应的测试要求

## Ticket 拆分

### T1: 数据模型与持久化基座（Foundation）
**优先级**: P0 - 所有其他 ticket 的基础
**依赖**: 无
**范围**:
- SQLite schema 定义（所有新表）
- 基础 Repository 层接口
- 启动时建表/迁移逻辑
- 基础事务支持

**测试重点**:
- 建表不破坏现有 pilot4mvp2 数据
- 外键约束正确
- UUID 主键生成

**不包含**: 完整的业务逻辑、复杂查询投影

---

### T2: Run 命令扩展与澄清状态机（Clarification Commands）
**优先级**: P0 - 入口契约
**依赖**: T1
**范围**:
- `CreateRunRequest` 增加 `command` 判别联合
- `clarification.submit_input` 命令处理
- `clarification.close` 命令处理
- 澄清状态机（计数、封盘逻辑）
- 幂等性保证

**测试重点**:
- 第 3 次 accepted 自动封盘
- 第 5 次 non-accepted 自动封盘
- 封盘后拒绝新文本
- 重复 close 命令幂等
- 同一 input_id + 不同正文返回 409

**不包含**: LLM 分类逻辑（可用 mock）、完整 LangGraph 编排

---

### T3: 澄清与规格生成工作流（Clarification & Spec Workflow）
**优先级**: P0 - 核心生成链路
**依赖**: T2
**范围**:
- LangGraph workflow: `clarification_spec.py`
- 分类输入节点（classify_input）
- 提取愿望项节点（extract_wish_items）
- 冻结 Requirements 节点
- 生成 DestinationSpec 节点
- 校验并锁定 Spec 节点

**测试重点**:
- Requirements 条目包含来源、执行度、rationale
- Spec 必须恰好两个 ScenePlan
- 两个 ScenePlan 的宠物行为/状态不同
- 冻结后不可修改
- Agent inference 必须有 rationale

**不包含**: 真实 LLM 调用（可用 fixture）、完整重试机制

---

### T4: 目的地协调器与跨阶段调度（Destination Coordinator）
**优先级**: P1 - 编排核心
**依赖**: T1
**范围**:
- `DestinationCoordinatorService` 实现
- 根据 Repository 里程碑调度阶段
- 启动恢复逻辑（扫描非终态 Destination）
- Worker 接入 coordinator

**测试重点**:
- 已提交里程碑不重做
- checkpoint 落后时以 Repository 为准
- 非终态 Destination 自动恢复

**不包含**: 完整 LangGraph 编排、并行场景生成

---

### T5: 共享环境生成（Shared Environment）
**优先级**: P1 - 场景生成前置
**依赖**: T3, T4
**范围**:
- `generation_planning.py` workflow
- 创建两个 ScenePlan
- 生成共享环境母图
- 原子提交 SharedEnvironmentArtifact
- 记录 SHA-256 和尺寸

**测试重点**:
- 两个 Scene 引用同一母图 file_id 与 SHA
- 母图通过格式/尺寸/哈希校验
- 不可变 artifact

**不包含**: 真实图像生成（可用 fixture）、定位逻辑

---

### T6: 场景定位与圆检测（Scene Localization & Circle Detection）
**优先级**: P0 - 关键技术路径
**依赖**: T5
**范围**:
- 生成定位参考图（带黑圈）
- 确定性圆心检测算法
- 圆检测校验（唯一、非 NaN、边界内）
- 定位重试逻辑（最多 3 attempts）
- `interaction_circle.py` 纯函数

**测试重点**:
- 恰好一个合法黑圈时检测得到稳定整数圆心
- 无圆、多圆、NaN/无限值、越界均拒绝
- 定位最多 3 attempts
- 禁止模板坐标兜底

**不包含**: 真实图像生成、最终场景生成

---

### T7: Mask 生成与场景最终生成（Scene Generation）
**优先级**: P0 - 交付核心
**依赖**: T6
**范围**:
- `scene_generation.py` workflow
- 从圆心和固定直径生成 Mask
- 打洞参考图生成
- 最终场景生成
- InteractionZone 计算（pixel_top_left）
- 原子提交 SceneArtifact

**测试重点**:
- 同一圆心与配置直径生成字节稳定 Mask
- InteractionZone 与生成 Mask 使用同一 center/radius
- render asset、circle、hash、引用全部完成后才 ready
- 技术失败最多 3 attempts

**不包含**: 审美判断、真实图像生成（可用 fixture）

---

### T8: Manifest 投影与只读 API（Read-only APIs）
**优先级**: P1 - Unity 消费契约
**依赖**: T7
**范围**:
- `GET /api/v1/destinations/{destination_id}` 
- `GET /api/v1/destinations/{destination_id}/scenes/{scene_id}`
- DestinationManifest 投影逻辑
- SceneArtifact 返回
- ETag 与 304 支持
- 坐标转换（pixel_top_left → pixel_bottom_left）

**测试重点**:
- Agent 内部坐标为 pixel_top_left
- 公开 InteractionZone 为 pixel_bottom_left
- 服务端只做一次 `canvas_height_px - 1 - y` 转换
- ETag 与不可变内容一致
- 重复 GET 不创建 attempt，不增加 delivery_revision

**不包含**: 完整权限校验、复杂轮询逻辑

---

### T9: 零愿望 Fallback 路径（Zero-Wish Fallback）
**优先级**: P2 - 容错路径
**依赖**: T2, T3
**范围**:
- accepted_wish_count=0 时选择独立 fallback
- fallback_destination_id 生成
- 不调用 LLM 自由推断
- Manifest 中 fallback_destination_id 字段

**测试重点**:
- 封盘时 accepted=0 选择独立 fallback
- fallback 拥有独立身份
- 不伪装成本轮成功生成

**不包含**: 完整 fallback 内容库

---

### T10: 错误处理与稳定错误码（Error Handling）
**优先级**: P2 - 生产质量
**依赖**: T2-T9
**范围**:
- 新增稳定错误码（至少 11 个）
- 错误 envelope 统一格式
- Provider 异常映射
- 日志脱敏（API key、敏感信息）

**测试重点**:
- 所有新错误码覆盖
- 内部异常不泄露给 Unity
- 确定性错误不可重试

**不包含**: 完整监控集成

---

### T11: 业务事件与可观测性（Observability）
**优先级**: P2 - 诊断支持
**依赖**: T2-T9
**范围**:
- Run Events 结构化业务事件
- 至少 13 个关键事件类型
- 事件包含必要上下文（destination_id、scene_id、attempt）

**测试重点**:
- 关键里程碑都有对应事件
- 事件包含可追溯信息

**不包含**: 外部监控系统集成、完整 JSONL

---

### T12: 端到端验收测试（E2E Acceptance）
**优先级**: P1 - 质量门槛
**依赖**: T1-T11
**范围**:
- 固定 Provider/fixture 端到端场景
- 从创建 Session 到 Done 的完整流程
- 成功路径（两个场景都 ready）
- 失败路径（第二个场景定位三次均多圆）

**测试重点**:
- 完整闭环可执行
- Manifest 正确投影
- 两个场景共享环境母图
- publish_eligible 正确计算

**不包含**: 真实 LLM/Provider 集成测试

---

## 实施顺序建议

**Phase 1 - 核心链路（并行）**:
- T1 → T2 → T3（澄清与规格）
- T1 → T4（协调器）

**Phase 2 - 生成链路（串行）**:
- T5 → T6 → T7（环境 → 定位 → 场景）

**Phase 3 - 交付与容错（并行）**:
- T8（只读 API）
- T9（Fallback）
- T10（错误处理）
- T11（可观测性）

**Phase 4 - 验收**:
- T12（E2E）

## 预计工作量

- P0 tickets: 5 个（T1-T3, T6-T7）- 约 5-7 天
- P1 tickets: 4 个（T4-T5, T8, T12）- 约 3-4 天  
- P2 tickets: 3 个（T9-T11）- 约 2-3 天

**总计**: 约 10-14 天（假设 1-2 人并行）
