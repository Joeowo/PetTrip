# T2 代码评审修复 Tickets

本目录包含针对 PR #32 (T2: Run 命令扩展与澄清状态机) 代码评审发现的修复 tickets。

## 评审来源

- **评审日期**: 2026-08-23
- **评审范围**: PR #32 (`ce57451...55dd72b`)
- **评审方法**: 双轴评审（Standards + Spec）
- **相关 Issue**: #14 (T2: Run 命令扩展与澄清状态机)

## Tickets 列表

按依赖顺序排列（可并行执行的除外）：

1. **01-fix-error-code-mismatch.md** - 修复错误码不匹配（规范违规，高优先级）
   - 状态: ✅ 已发布为 GitHub Issue #44
   - 阻塞: 无
   
2. **02-fix-domain-storage-dependency.md** - 修复 Domain 层依赖 Storage 层异常（架构违规，高优先级）
   - 状态: 📝 本地文件（待手动发布）
   - 阻塞: 无（建议在 01 之后执行以避免冲突）
   
3. **03-refactor-duplicate-exception-handling.md** - 提取重复的异常处理逻辑（代码质量，中优先级）
   - 状态: 📝 本地文件（待手动发布）
   - 阻塞: 01

## 发布到 GitHub

Ticket 01 已发布为 Issue #44。

对于 Tickets 02 和 03，你可以：

1. **手动创建 GitHub Issues**: 复制 markdown 内容到 GitHub issue 创建页面
2. **使用 gh CLI**: 运行以下命令

```bash
# Ticket 02
gh issue create --title "修复 Domain 层依赖 Storage 层异常（违反分层架构）" \
  --body-file .scratch/t2-code-review-fixes/issues/02-fix-domain-storage-dependency.md

# Ticket 03
gh issue create --title "重构 API 层：提取重复的异常处理逻辑" \
  --body-file .scratch/t2-code-review-fixes/issues/03-refactor-duplicate-exception-handling.md
```

3. **添加阻塞关系** (如果 GitHub 支持 issue dependencies):

```bash
# 获取 Issue #44 的数据库 ID
ISSUE_44_ID=$(gh api repos/Joeowo/PetTrip/issues/44 --jq .id)

# 假设 Ticket 03 创建为 Issue #46
ISSUE_46_ID=$(gh api repos/Joeowo/PetTrip/issues/46 --jq .id)

# 添加阻塞关系：Issue #46 被 #44 阻塞
gh api --method POST repos/Joeowo/PetTrip/issues/46/dependencies/blocked_by \
  -F issue_id=$ISSUE_44_ID
```

## 未包含的问题

以下代码评审发现**未创建 ticket**，原因已在评审报告中说明：

- **过长的路由处理函数（103 行）**: 设计权衡，T2 阶段保持完整的命令路由逻辑在一个函数中有助于理解
- **Feature Envy 和 Primitive Obsession**: 判断性代码异味，不影响功能，等系统更成熟时再考虑
- **Scope creep 功能**: `idempotent_replay`、`close_request_id` 等增强了系统可观测性，建议保留
