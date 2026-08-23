# ADR 0002: 轻量分层架构 - 从扁平结构升级到职责分层

**状态**: 已采纳  
**日期**: 2026-08-23  
**决策者**: 项目团队  
**相关**: [ADR 0001](0001-codebase-restructure-production-vs-prototypes.md)（前置决策：最小调整原则）

## 背景

在 Issue #1 (spec-to-tickets) 完成后、实施开始前，我们评估了 `agent_service/` 的代码组织。当前结构（ADR 0001 建立）采用"最小调整"原则：13 个模块扁平放置在根目录，虽然满足了快速原型验证的需求，但随着功能增长暴露了以下问题：

1. **职责边界模糊**：`storage.py` (674 行) 同时包含 SQLite CRUD、Run 状态机、事务管理、幂等性逻辑
2. **缺乏统一接口**：`chat_provider.py` 和 `image_provider.py` 各自实现，测试需要分别 mock
3. **依赖关系隐蔽**：扁平结构掩盖了模块间的依赖层次
4. **测试困难**：业务逻辑与数据访问耦合，单元测试需要完整数据库

应用**删除测试**（Deletion Test）：如果删除 `storage.py`，其复杂度会散布到 N 个调用方，说明它承担了过多职责但缺乏清晰的接口设计。

## 决策

采用**轻量分层架构**，基于**深度模块**（Deep Modules）和**渐进式披露**（Progressive Disclosure）原则，将 13 个扁平模块重组为 5 个职责层：

```
agent_service/
├── api/          # HTTP 协议适配层（薄层）
├── domain/       # 业务逻辑核心（深模块）
├── adapters/     # 外部服务接口（统一 Protocol）
├── storage/      # 数据持久化（Repository Pattern）
└── shared/       # 跨层通用设施
```

### 核心设计决策

#### 1. 拆分 `storage.py` (674 行)
- **CRUD 操作** → `storage/database.py`（数据访问）
- **状态机逻辑** → `domain/runs.py`（业务流程）
- **数据模型和异常** → `storage/models.py`（共享定义）

**理由**：Run 的状态转换（queued → running → succeeded/failed）是业务规则，不应与 SQL 查询混在一起。拆分后：
- `domain/runs.py` 暴露简单接口（`create_run()`, `complete_run_success()`, `mark_run_failed()`）
- 隐藏复杂实现（幂等性检查、附件验证、消息创建、事件记录、会话更新）
- **深度模块**：小接口 + 大实现 = 高杠杆率

#### 2. 统一 Provider 接口
- 定义 `LLMAdapter` 和 `ImageAdapter` Protocol
- `chat_provider.py` → `adapters/llm.py`（实现 `LLMAdapter`）
- `image_provider.py` → `adapters/image.py`（实现 `ImageAdapter`）

**理由**：
- **测试性**：domain 层通过 Protocol 调用，可用 Mock 替换真实 Provider
- **扩展性**：未来添加新 Provider（如 Anthropic、本地模型）只需实现 Protocol
- **清晰的 Seam**：一个 Adapter = 假设的接缝（hypothetical seam），两个 = 真实接缝（real seam）

#### 3. 依赖规则
```
api/ → domain/ → adapters/storage/ → shared/
```
- **内层不依赖外层**：domain 不知道 FastAPI 存在
- **domain 是核心**：业务逻辑不受框架和基础设施影响
- **依赖注入**：domain 函数接受 `Database`、`LLMAdapter` 等依赖参数

#### 4. 向后兼容层
- `storage/__init__.py` 重新导出 `Storage` 类
- `Storage` 类委托给新的 `Database` 和 `domain.runs`
- 现有测试无需修改，渐进式迁移

### 模块映射

| 原位置 | 新位置 | 理由 |
|--------|--------|------|
| `app.py` | `api/app.py` | HTTP 协议适配 |
| `schemas.py` | `api/schemas.py` | 请求/响应模型 |
| `auth.py` | `api/auth.py` | 认证中间件 |
| `chat_provider.py` | `adapters/llm.py` | 统一 LLM 接口 |
| `image_provider.py` | `adapters/image.py` | 统一图片接口 |
| `storage.py` (CRUD) | `storage/database.py` | 数据访问 |
| `storage.py` (状态机) | `domain/runs.py` | 业务逻辑 |
| `storage.py` (模型) | `storage/models.py` | 共享定义 |
| `worker.py` | `domain/worker.py` | Run 执行器 |
| `file_storage.py` | `storage/files.py` | 文件持久化 |
| `config.py` | `shared/config.py` | 配置管理 |
| `errors.py` | `shared/errors.py` | 错误定义 |
| `ids.py` | `shared/ids.py` | ID 生成 |
| `structured_output.py` | `shared/structured_output.py` | 结构化输出 |

## 实施

### 迁移步骤（8 个阶段）
1. ✅ 创建目录结构（api/, domain/, adapters/, storage/, shared/）
2. ✅ 移动纯工具模块（config, errors, ids → shared/）
3. ✅ 移动 API 层（app.py, schemas.py, auth.py → api/）
4. ✅ 移动 adapters（chat_provider → adapters/llm.py, image_provider → adapters/image.py）
5. ✅ 拆分 storage.py（database.py + domain/runs.py + models.py）
6. ✅ 移动剩余模块（worker, file_storage, structured_output）
7. ✅ 更新所有导入路径（api/, domain/, adapters/, storage/, shared/）
8. ✅ 更新文档（本 ADR + README）

### 验证结果
- ✅ 所有现有测试通过（19 个测试文件，无修改业务逻辑）
- ✅ `test_storage.py`: 7 个测试全部通过
- ✅ `test_worker.py`: 3 个测试全部通过
- ✅ `test_session1_api.py`: 4 个 API 集成测试通过

### 工作量
- **预估**: 2-4 小时
- **实际**: ~3 小时（包含测试和文档）

## 后果

### 正面影响
1. **清晰的职责边界**：每层职责单一，模块深度提升
2. **更好的可测试性**：domain 层可独立测试，无需完整 HTTP 栈
3. **统一的适配器接口**：添加新 Provider 只需实现 Protocol
4. **渐进式深化空间**：未来可进一步提取 `RunLifecycle` 模块而不影响调用方
5. **AI 导航友好**：目录结构即文档，快速定位代码位置

### 负面影响
1. **导入路径变长**：从 `from .storage import Storage` 到 `from ..storage import Storage`
2. **向后兼容层开销**：`Storage` 类需要委托给 `Database` 和 `domain.runs`
3. **学习成本**：新成员需要理解分层架构和依赖规则

### 风险缓解
- **向后兼容层**：现有代码无需立即修改，渐进式迁移
- **测试覆盖**：所有原有测试通过，保证行为不变
- **文档同步**：更新 README 和本 ADR，记录迁移理由

## 设计原则

本次重构应用了以下设计原则（来自 *A Philosophy of Software Design* 和渐进式披露思想）：

1. **深度模块**（Deep Modules）  
   - 小接口 + 大实现 = 高杠杆率
   - `domain.runs.create_run()` 暴露一次函数调用，隐藏 7 步复杂流程

2. **删除测试**（Deletion Test）  
   - 如果删除一个模块会导致复杂度散布到 N 个调用方，说明它在挣钱（earning its keep）
   - `storage.py` 未通过删除测试 → 拆分为 database + domain + models

3. **接口即测试面**（The Interface Is the Test Surface）  
   - Protocol 定义了 Adapter 的契约，测试只需验证接口行为
   - Mock `LLMAdapter` 比 Mock 具体 Provider 更简单

4. **一个 Adapter = 假设的接缝，两个 = 真实接缝**  
   - 单一 Provider 时过早抽象，有两个时才建立统一接口
   - 我们已有 OpenAI Chat 和 Image 两个 Provider → 建立 Protocol 时机成熟

5. **渐进式披露**（Progressive Disclosure）  
   - **现在暴露少量**：建立清晰的层边界（api/, domain/, adapters/, storage/, shared/）
   - **未来深入时按需细化**：如需要可进一步提取 `RunLifecycle`、`MessageBuilder` 等模块
   - **不过度抽象**：不在项目初期引入复杂的 DDD 模式或微服务架构

## 未来工作

本次重构建立了基础架构，但保留了渐进深化的空间：

1. **进一步深化 domain 层**（当需要时）
   - 提取 `RunLifecycle` 模块（状态转换逻辑）
   - 提取 `MessageBuilder` 模块（消息组装逻辑）
   - 引入 `RunRepository` Protocol（解耦 domain 与具体数据库）

2. **完善 Adapter 层**（当添加新 Provider 时）
   - 实现 `AnthropicAdapter`（Anthropic Claude）
   - 实现 `LocalLLMAdapter`（本地模型）
   - 添加 Adapter 健康检查和降级策略

3. **优化测试分层**（当测试复杂度增加时）
   - domain 层单元测试（纯逻辑，无 I/O）
   - storage 层集成测试（真实数据库）
   - api 层端到端测试（完整 HTTP 栈）

## 参考

- [ADR 0001: 代码库重组 - production vs prototypes](0001-codebase-restructure-production-vs-prototypes.md)
- *A Philosophy of Software Design* by John Ousterhout（深度模块、删除测试）
- Layered Architecture Pattern（分层架构模式）
- Repository Pattern（仓储模式）
- Adapter Pattern（适配器模式）
