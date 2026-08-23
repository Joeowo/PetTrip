# 03: 重构 API 层：提取重复的异常处理逻辑

**What to build:** `api/app.py` 中的幂等键冲突异常处理逻辑不再重复，提升代码可维护性。

**Blocked by:** 01-fix-error-code-mismatch.md (错误码修复后再重构，避免同时修改同一区域)

**Status:** ready-for-agent

## Parent

Issue #14 (T2: Run 命令扩展与澄清状态机) - 代码评审发现的代码质量问题

## Context

代码评审发现：`api/app.py` 中存在重复的幂等键冲突异常处理代码，出现在至少 2 个位置（lines 88-93 和 117-122）：

```python
except IdempotencyKeyReusedError as exc:
    raise ApiError(
        IDEMPOTENCY_KEY_REUSED,
        "Idempotency-Key 已用于不同请求。",
        status=409,
    ) from exc
```

这是典型的 "Duplicated Code" 异味，违反 DRY 原则。

## Acceptance criteria

- [ ] 提取幂等键异常处理为辅助函数（如 `_handle_idempotency_error(exc) -> ApiError`）
- [ ] 在所有幂等键冲突点调用该辅助函数（当前至少 2 处）
- [ ] 所有现有测试继续通过
- [ ] 代码行数减少（消除重复代码）
