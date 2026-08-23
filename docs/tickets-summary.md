# Implementation Tickets Summary

基于 issue #10 的实现规格，已成功创建 12 个实现 tickets。

## Created Issues

### Phase 1 - 核心链路（P0）

- **#13**: [T1: 数据模型与持久化基座](https://github.com/Joeowo/PetTrip/issues/13)
  - SQLite schema、Repository 层、启动迁移
  - 依赖：无
  - 状态：Ready to start

- **#14**: [T2: Run 命令扩展与澄清状态机](https://github.com/Joeowo/PetTrip/issues/14)
  - Run 命令联合类型、澄清状态机、封盘逻辑
  - 依赖：T1
  - 状态：Blocked by #13

- **#15**: [T3: 澄清与规格生成工作流](https://github.com/Joeowo/PetTrip/issues/15)
  - LangGraph workflow、Requirements 生成、DestinationSpec 生成
  - 依赖：T2
  - 状态：Blocked by #14

### Phase 1 - 协调器（P1）

- **#16**: [T4: 目的地协调器与跨阶段调度](https://github.com/Joeowo/PetTrip/issues/16)
  - DestinationCoordinatorService、启动恢复、Worker 接入
  - 依赖：T1
  - 状态：Blocked by #13

### Phase 2 - 生成链路（P0/P1）

- **#17**: [T5: 共享环境生成](https://github.com/Joeowo/PetTrip/issues/17)
  - Generation Planning Workflow、共享环境母图生成
  - 依赖：T3, T4
  - 状态：Blocked by #15, #16

- **#18**: [T6: 场景定位与圆检测](https://github.com/Joeowo/PetTrip/issues/18)
  - 定位参考图生成、确定性圆心检测、定位重试
  - 依赖：T5
  - 状态：Blocked by #17

- **#19**: [T7: Mask 生成与场景最终生成](https://github.com/Joeowo/PetTrip/issues/19)
  - Scene Generation Workflow、Mask 生成、SceneArtifact 提交
  - 依赖：T6
  - 状态：Blocked by #18

### Phase 3 - 交付与容错（P1/P2）

- **#20**: [T8: Manifest 投影与只读 API](https://github.com/Joeowo/PetTrip/issues/20)
  - Destination Manifest API、Scene Artifact API、坐标转换
  - 依赖：T7
  - 状态：Blocked by #19

- **#21**: [T9: 零愿望 Fallback 路径](https://github.com/Joeowo/PetTrip/issues/21)
  - 零愿望检测、Fallback 选择
  - 依赖：T2, T3
  - 状态：Blocked by #14, #15

- **#22**: [T10: 错误处理与稳定错误码](https://github.com/Joeowo/PetTrip/issues/22)
  - 稳定错误码、错误 envelope、日志脱敏
  - 依赖：T2-T9
  - 状态：Blocked by previous tickets

- **#23**: [T11: 业务事件与可观测性](https://github.com/Joeowo/PetTrip/issues/23)
  - Run Events 扩展、结构化业务事件
  - 依赖：T2-T9
  - 状态：Blocked by previous tickets

### Phase 4 - 验收（P1）

- **#24**: [T12: 端到端验收测试](https://github.com/Joeowo/PetTrip/issues/24)
  - 成功路径端到端、失败路径端到端
  - 依赖：T1-T11
  - 状态：Blocked by all previous tickets

## Dependency Graph

```
T1 (Foundation) ──┬──> T2 (Commands) ──> T3 (Workflow) ──┬──> T5 (Environment) ──> T6 (Localization) ──> T7 (Scene) ──> T8 (API)
                  │                                       │
                  └──> T4 (Coordinator) ─────────────────┘
                  
T2, T3 ──> T9 (Fallback)
T2-T9 ──> T10 (Errors), T11 (Observability)
T1-T11 ──> T12 (E2E)
```

## Implementation Order Recommendation

**Week 1 - Foundation & Entry**:
1. Start: T1 (Foundation)
2. Then: T2 (Commands) + T4 (Coordinator) in parallel
3. Then: T3 (Workflow)

**Week 2 - Generation Pipeline**:
4. Start: T5 (Environment)
5. Then: T6 (Localization)
6. Then: T7 (Scene Generation)

**Week 3 - Delivery & Quality**:
7. Start: T8 (API) + T9 (Fallback) in parallel
8. Then: T10 (Errors) + T11 (Observability) in parallel
9. Finally: T12 (E2E)

## Key Features of These Tickets

✅ **统一的 Context 块** - 每个 ticket 开头都有固定的 source of truth 引用
✅ **快速主链路原则** - 明确区分核心功能与护栏细节，避免过度投入
✅ **清晰的边界** - Scope 明确"包含"与"不包含"
✅ **测试内置** - 每个 ticket 包含具体的测试要求
✅ **冲突上报机制** - Context 块要求遇到文档冲突时停止并上报

## Next Steps

1. **开始实施**: 从 T1 开始，按推荐顺序执行
2. **并行开发**: T2+T4 可以并行，T8+T9 可以并行，T10+T11 可以并行
3. **持续集成**: 每个 ticket 完成后立即跑回归测试
4. **最终验收**: T12 作为质量门槛，确保整个闭环正常工作

## Documentation Created

- `docs/adr/README.md` - 架构决策记录索引
- `docs/contracts/README.md` - Agent-Unity 契约索引
- `docs/implementation-tickets-plan.md` - 详细拆分方案

所有原始 ticket 文档保存在：`$CLAUDE_JOB_DIR/tmp/ticket-t*.md`
