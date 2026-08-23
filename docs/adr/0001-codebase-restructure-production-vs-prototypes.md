# ADR 0001: 代码库重组 - 生产代码与原型分离

## Status

Accepted

## Context

PetTrip 项目从原型验证阶段过渡到正式开发阶段（Issue #1: 静态目的地纵向闭环）。当前代码库存在以下问题：

1. **目录命名混乱**：`pilot4mvp`, `pilot4mvp2`, `pilot4mvp3` 三个目录，命名无法体现其角色
2. **生产/原型边界模糊**：`pilot4mvp2/` 最初是原型，但现在包含生产级的 Agent Service 代码
3. **历史包袱**：`pilot4mvp/` 的四会话验证已完成并合并，但目录仍在根目录占用空间
4. **工作流不清晰**：新的 Issue #1 tickets 应该在哪里工作？

### 三个 pilot 目录的实际状态

- **pilot4mvp**: 四会话技术栈联通测试，已完成并合并到 main（commit `8ab17db`），纯历史参考
- **pilot4mvp2**: Agent Service 的完整实现（13 个核心模块、19 个测试文件、Session 1-7 验证），已部分合并到 main
- **pilot4mvp3**: Mask 稳定性验证实验，包含**验证成功的核心路径**（在 worktree `issue12-experiment-prototype`）

### 已合并的工作

- T1 (Issue #13): 数据模型与持久化基座，已通过 PR #25 合并到 main
- T1 的代码位于 `pilot4mvp2/agent_service/destination_storage.py`

## Decision

我们决定执行代码库重组，建立清晰的生产代码和原型边界：

### 1. 新的生产代码结构

创建 `agent_service/` 作为唯一的生产代码目录：

```
agent_service/
├── app.py
├── chat_provider.py
├── image_provider.py
├── worker.py
├── structured_output.py
├── storage.py
├── destination_storage.py    # T1 新增
├── file_storage.py
├── config.py
├── auth.py
├── schemas.py
├── ids.py
├── errors.py
├── run_server.py
├── tests/                     # 所有测试移到这里
│   ├── test_session1_*.py
│   ├── test_session2_*.py
│   └── ...
├── scripts/                   # 服务相关脚本
└── requirements.txt
```

**设计原则**：
- **最小调整**：保持模块扁平结构，不进行内部重构
- **完整迁移**：从 `pilot4mvp2/agent_service/` 和 `pilot4mvp2/tests/` 完整迁移
- **测试内聚**：测试和代码放在同一顶层目录

### 2. 原型归档

所有 pilot 目录移到 `prototypes/`：

```
prototypes/
├── pilot4mvp/          # 四会话验证（已完成）
├── pilot4mvp2/         # Agent Service 原型（代码已迁移）
└── pilot4mvp3/         # Mask 稳定性实验
```

### 3. pilot4mvp3 的特殊处理

由于 pilot4mvp3 包含验证成功的核心路径，需要**拆分处理**：

1. **核心验证结果文档化**：
   - 提取关键的验证路径、配置、Prompt 到 `docs/validated-paths/mask-stability/`
   - 包含：实验设计、验证结果、可复现配置
   
2. **实验脚本归档**：
   - 完整的 pilot4mvp3 目录移到 `prototypes/pilot4mvp3/`
   - 保留历史可追溯性

### 4. 配置和脚本的处理

从 `pilot4mvp2/` 迁移到合适位置：

- `scripts/` → `agent_service/scripts/`
- `requirements.txt` → `agent_service/requirements.txt`
- `.env.local` → `agent_service/.env.example`（脱敏后作为模板）
- `*.md` 文档 → `docs/agent_service/`

### 5. 未来工作流

所有 Issue #1 的后续 tickets (T2, T3, ...) 遵循：

1. **从 main 创建分支**：每个 ticket 一个独立的 feature 分支
2. **使用 worktree**：`worktree-<branch-name>` 进行隔离开发
3. **在 agent_service/ 工作**：所有新代码添加到 `agent_service/`
4. **PR 合并到 main**：通过 PR 审查后合并

## Consequences

### Positive

1. **清晰的边界**：生产代码（`agent_service/`）和原型（`prototypes/`）职责明确
2. **可预测的工作流**：新 tickets 明确知道在哪里工作
3. **历史可追溯**：原型归档保留，重要验证结果文档化
4. **简化认知负担**：根目录不再有 3 个 pilot 目录
5. **符合工程最佳实践**：单一服务目录、测试内聚

### Negative

1. **一次性成本**：需要移动文件、更新导入路径、更新文档引用
2. **正在进行的 worktrees**：11 个现有 worktrees 需要检查和清理
3. **Git 历史**：文件移动会影响 `git blame`（可以用 `git log --follow` 缓解）

### Risks

1. **路径引用失效**：文档、配置中的路径引用需要全面更新
2. **导入路径变化**：Python 导入路径从 `pilot4mvp2.agent_service` 改为 `agent_service`
3. **CI/CD 配置**：如果有 CI 配置，需要更新路径

### Mitigation

1. **在独立 worktree 中执行**：创建 `worktree-restructure-codebase`，不影响 main
2. **分步验证**：每步完成后运行测试，确保功能未破坏
3. **文档同步更新**：重组时同步更新所有路径引用
4. **Issue #1 记录**：作为 wayfinder:map 的一个决策记录

## Implementation Plan

1. ✅ 创建此 ADR
2. ✅ 在 Issue #1 中创建 ticket #26 "执行代码库重组"
3. ✅ 创建 worktree: `worktree-restructure-codebase` 从 main
4. 🔄 执行重组：
   - 创建 `agent_service/` 和 `prototypes/`
   - 移动 pilot4mvp, pilot4mvp2, pilot4mvp3
   - 从 pilot4mvp2 提取生产代码到 agent_service
   - 更新导入路径
   - 更新文档引用
   - 提取 pilot4mvp3 核心验证路径
5. ⏳ 运行所有测试验证
6. ⏳ 提交并创建 PR
7. ⏳ 合并到 main
8. ⏳ 清理已合并的 worktrees

## References

- Issue #1: 规划首个默认目的地静态纵向闭环
- Issue #26: 执行代码库重组
- Issue #13 (T1): 实现目的地生成数据模型与持久化基座
- PR #25: T1 实现（已合并）
- Memory: `pilot-worktree-workflow.md`
