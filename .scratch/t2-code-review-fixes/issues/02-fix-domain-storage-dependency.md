# 02: 修复 Domain 层依赖 Storage 层异常（违反分层架构）

**What to build:** Domain 层（`clarification.py`, `runs.py`）不再依赖 Storage 层的异常类型，符合 ADR 0002 的依赖规则（domain → storage，而非反向）。

**Blocked by:** None (可立即开始，但建议在 01 之后执行以避免冲突)

**Status:** ready-for-agent

## Parent

Issue #14 (T2: Run 命令扩展与澄清状态机) - 代码评审发现的架构违规

## Context

代码评审发现：`domain/clarification.py` 和 `domain/runs.py` 导入了 `storage.models` 的异常类：

```python
from ..storage.models import (
    ClarificationAlreadyClosedError,
    InputIdConflictError,
)
```

这违反了 ADR 0002 的依赖规则："domain is the core: business logic should not be affected by frameworks"。Domain 层不应该知道 Storage 层的具体异常类型。

**相关架构决策**: docs/adr/0002-layered-architecture-upgrade.md

## Acceptance criteria

- [ ] 将 `ClarificationAlreadyClosedError` 和 `InputIdConflictError` 从 `storage/models.py` 移至 `shared/errors.py`
- [ ] 更新 `domain/clarification.py` 和 `domain/runs.py` 的导入语句从 `shared.errors` 导入
- [ ] 更新 `storage/database.py` 的导入语句从 `shared.errors` 导入
- [ ] 所有现有测试继续通过
- [ ] 验证依赖方向：`domain/` 不再导入 `storage/` 的任何内容（除了 `Database` 类作为依赖注入）
