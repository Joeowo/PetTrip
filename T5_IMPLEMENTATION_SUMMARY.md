# T5 共享环境生成工作流 - 实施总结

## 实施内容

根据 Issue #17 的要求，成功实现了从锁定 DestinationSpec 到共享环境制品的完整生成链路。

### 1. 核心实现

#### 1.1 Generation Planning Workflow (`agent_service/workflows/generation_planning.py`)

实现了以下节点：
- **create_scene_plans_node**: 从 Repository 读取已创建的场景计划（T3 已完成）
- **validate_two_scene_invariants_node**: 验证两场景不变量
  - 必须恰好 2 个 ScenePlan
  - 两个场景的宠物行为/情绪/状态不能完全相同
- **create_prompt_snapshots_node**: 创建 Prompt 快照（简化版）
- **generate_shared_environment_node**: 生成共享环境母图
  - Mock 图像生成（2048x1152 渐变测试图）
  - 使用 FileStorage 原子写入 PNG
  - 在 Database 创建 file 记录
  - 记录尺寸、MIME、SHA-256、PromptSnapshot
  - 原子提交不可变 SharedEnvironmentArtifact

#### 1.2 数据存储扩展 (`agent_service/storage/destination_storage.py`)

扩展 `DestinationRepository` 类，新增方法：

**PromptSnapshot 相关**：
- `create_prompt_snapshot()`: 创建 Prompt 快照
- `get_prompt_snapshot()`: 获取 Prompt 快照

**SharedEnvironmentArtifact 相关**：
- `create_shared_environment_artifact()`: 创建共享环境制品
- `get_shared_environment_artifact()`: 获取共享环境制品

**OperationAttempt 相关**：
- `create_operation_attempt()`: 创建操作尝试记录
- `update_operation_attempt()`: 更新操作尝试状态
- `count_operation_attempts()`: 统计操作尝试次数

#### 1.3 不变量保护

严格实现了 Issue #10 第 4.6、8.1 节定义的不变量：

**SharedEnvironmentArtifact 不变量**：
1. 该对象不可变（一旦创建不能修改）
2. 仅供内部工作流使用（不直接暴露给 Unity）
3. 记录完整元数据（file_id、SHA-256、尺寸、PromptSnapshot）

**共享环境验收**：
1. 两个场景必须引用同一个不可变 SharedEnvironmentArtifact
2. 引用完全相同的环境母图 `file_id` 与 SHA-256
3. 幂等性保护：重复运行返回已有 artifact

**重试不变量**：
1. 环境生成失败时可重试（最多 3 attempts：0, 1, 2）
2. 重试保持 DestinationSpec 和 ScenePlan 不变

### 2. 测试覆盖

实现了 Issue #10 第 15.3 节要求的测试用例（共享环境部分）：

**测试文件**: `agent_service/tests/workflows/test_generation_planning.py`

✅ **测试 1**: 两个 Scene 引用同一母图 file_id 与 SHA  
✅ **测试 2**: 母图原子落盘并通过格式/尺寸/哈希校验  
✅ **测试 3**: SharedEnvironmentArtifact 提交后不可修改（幂等性）  
✅ **测试 4**: 环境生成失败时可重试（最多 3 attempts）  
✅ **测试 5**: 重试保持 DestinationSpec 和 ScenePlan 不变  
✅ **测试 6**: 工作流验证两场景不变量  
✅ **测试 7**: PromptSnapshot 正确创建并关联到 SharedEnvironmentArtifact  
✅ **测试 8**: 端到端完整工作流测试

**测试结果**: 8 passed in 16.75s

### 3. 快速主链路原则

按照 Issue #17 "快速主链路原则" 实施：

✅ **工作流跑通**: 节点之间正确传递状态  
✅ **关键里程碑**: SharedEnvironmentArtifact、PromptSnapshot 正确写入 Repository  
✅ **图像生成简化**: 使用 mock 函数生成测试图片（2048x1152 渐变背景）  
✅ **基础重试逻辑**: 实现了最多 3 次重试机制

### 4. 与现有基座的集成

✅ 复用现有 `DestinationRepository` 和 `LocalImageStorage`  
✅ 兼容现有数据库 schema（destinations, files, runs 表）  
✅ 遵循 ADR-0002 分层架构（domain/storage/workflows）  
✅ 所有现有测试通过

## 完成情况

根据 Issue #17 的 Definition of Done：

- [x] Generation Planning Workflow 可执行端到端
- [x] 环境母图生成并存储到 FileStorage
- [x] SharedEnvironmentArtifact 正确提交
- [x] SHA-256 和元数据记录正确
- [x] 两个 ScenePlan 都引用同一 artifact
- [x] 本 ticket 的测试用例通过（8/8 passed）
- [x] 基础重试逻辑工作

## 后续工作

根据 Issue #17 延期的内容，以下留待后续阶段：

1. **真实图像生成**: 将 mock 函数替换为实际的图片生成 Provider 调用
2. **完整 PromptSnapshot**: 当前简化版，需要从 DestinationSpec 渲染完整 Prompt
3. **复杂重试策略**: 当前是基础版，可以添加更智能的重试策略
4. **定位逻辑**: 留给 T6（黑圈检测和定位）

## 文件清单

### 新增文件
- `agent_service/workflows/generation_planning.py` (367 行)
- `agent_service/tests/workflows/test_generation_planning.py` (527 行)

### 修改文件
- `agent_service/storage/destination_storage.py`: 新增 339 行（PromptSnapshot、SharedEnvironmentArtifact、OperationAttempt CRUD）

## 验证命令

```bash
# 运行工作流测试
python -m pytest agent_service/tests/workflows/test_generation_planning.py -v

# 运行所有测试
python -m pytest agent_service/tests/ -v
```

## 技术亮点

1. **不可变制品**: SharedEnvironmentArtifact 一旦创建不可修改，保证数据一致性
2. **幂等性保护**: 重复运行工作流返回已有 artifact，避免重复生成
3. **原子操作**: 文件写入和数据库记录在事务中原子提交
4. **重试机制**: 支持最多 3 次重试，保持 Spec/Plan 不变
5. **测试覆盖**: 8 个测试覆盖所有核心场景和不变量

## 总结

T5 共享环境生成工作流已成功实现，所有核心不变量得到保护，测试覆盖完整。实现遵循"快速主链路原则"，为后续的真实图像生成和完整功能扩展奠定了坚实基础。

提交: 461dccc - feat(T5): 实现共享环境生成工作流 (Issue #17)
