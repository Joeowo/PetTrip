# 01: 修复错误码不匹配：input_id 冲突应返回 IDEMPOTENCY_KEY_REUSED

**What to build:** 当同一 `input_id` 提交不同内容时，API 返回规范要求的 `IDEMPOTENCY_KEY_REUSED` 错误码（409），而非当前的 `INPUT_ID_CONFLICT`，使客户端行为符合 Issue #14 规范。

**Blocked by:** None (可立即开始)

**Status:** ready-for-agent

## Parent

Issue #14 (T2: Run 命令扩展与澄清状态机) - 代码评审发现的规范违规
GitHub issue: https://github.com/Joeowo/PetTrip/issues/44

## Context

代码评审发现：Issue #14 规范要求"同一 input_id + 不同正文返回 409 IDEMPOTENCY_KEY_REUSED"，但当前实现返回 `INPUT_ID_CONFLICT` 错误码。这导致客户端需要处理规范外的错误类型。

**问题位置**:
- `shared/errors.py:19` - 定义了规范外的 `INPUT_ID_CONFLICT` 错误码
- `api/app.py:101-105` - 抛出 `INPUT_ID_CONFLICT` 而非 `IDEMPOTENCY_KEY_REUSED`

## Acceptance criteria

- [ ] 删除 `shared/errors.py` 中的 `INPUT_ID_CONFLICT` 错误码定义
- [ ] `api/app.py` 中 input_id 冲突时抛出 `IdempotencyKeyReusedError`
- [ ] 测试 `test_t2_clarification_state_machine.py::test_idempotency_input_id_different_text` 验证返回 `IDEMPOTENCY_KEY_REUSED`
- [ ] 所有现有测试继续通过
