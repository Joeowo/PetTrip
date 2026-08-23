# Issue #17 更新 - Prompt 结构参考

## 添加到 References 部分

在 Issue #17 的 `## References` 部分添加以下内容：

### Prompt 结构参考

原型验证中的 prompt layers 分层结构可作为环境母图生成的参考：

**参考文件**：`.claude/worktrees/issue12-experiment-prototype/pilot4mvp3/issue12/runs/issue12-preflight-001/evidence.html`

**关键 Prompt 分层**：
- `fixed_boundaries`：固定边界约束
- `style_description`：画风描述
- `composition_prompt`：构图提示
- `pet_anchor`：宠物锚点描述
- `negative_prompt`：负面提示

这些分层结构展示了如何组织环境母图生成 prompt，确保画风、构图和宠物位置的协调。

## 实施说明

由于 Issue 本身在 GitHub 上，建议在 Issue #17 的评论中添加以上内容，或者更新 Issue 描述的 References 部分。

手动操作步骤：
```bash
gh issue comment 17 --body "## Prompt 结构参考

原型验证中的 prompt layers 分层结构可作为环境母图生成的参考：

**参考文件**：\`.claude/worktrees/issue12-experiment-prototype/pilot4mvp3/issue12/runs/issue12-preflight-001/evidence.html\`

**关键 Prompt 分层**：
- \`fixed_boundaries\`：固定边界约束
- \`style_description\`：画风描述
- \`composition_prompt\`：构图提示
- \`pet_anchor\`：宠物锚点描述
- \`negative_prompt\`：负面提示

这些分层结构展示了如何组织环境母图生成 prompt，确保画风、构图和宠物位置的协调。

_(由 Issue #36 T3.1 添加)_"
```
