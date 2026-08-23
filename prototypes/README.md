# PetTrip Prototypes

本目录包含 PetTrip 项目的所有原型和实验验证。这些代码已完成历史使命，归档保留以供参考。

## 目录说明

### pilot4mvp/
**四会话技术栈联通测试** - 已完成并合并到 main (commit `8ab17db`)

验证目标：
- Unity ↔ Python ↔ OpenAI 完整链路
- Session 1: Unity 加载人工 Snapshot
- Session 2: Python 服务生成 Snapshot
- Session 3: 接入 OpenAI 图片生成
- Session 4: SQLite 持久化与重放

**状态**: ✅ 已完成，纯历史参考

---

### pilot4mvp2/
**Agent Service 原型** - 生产代码已迁移到 `agent_service/`

包含内容：
- 13 个核心模块的原始实现
- 19 个测试文件
- Session 1-7 的完整验证
- Flowise 探索（已弃用）

**状态**: ✅ 生产代码已提取，保留作为演化参考

**迁移记录**: 
- 代码 → `agent_service/`
- 文档 → `docs/agent_service/`
- 参考 ADR 0001

---

### pilot4mvp3/
**Mask 稳定性验证实验**

验证目标：
- "纯场景图 + 目标元素 → 固定大小 Mask → 角色场景" 链路可行性
- 8 个任务组、32 个实验单元、64 次图片调用
- **包含验证成功的核心路径** ⭐

**状态**: ✅ 实验完成，核心路径已文档化

**核心验证路径**: `docs/validated-paths/mask-stability/`
- 实验设计规格
- 验证结果和评审
- 可复现配置

---

## 使用指引

### 查看历史实现
```bash
# 查看原型代码
cd prototypes/pilot4mvp2/agent_service/

# 对比生产代码
diff -r prototypes/pilot4mvp2/agent_service/ agent_service/
```

### 追溯 Git 历史
```bash
# 文件移动后追溯历史
git log --follow agent_service/storage.py

# 查看原型时期的提交
git log prototypes/pilot4mvp2/
```

### 参考验证路径
pilot4mvp3 的核心验证路径已提取到文档，建议直接查看：
```bash
cd docs/validated-paths/mask-stability/
cat mask-stability-validation-spec.md
```

---

## 相关文档

- **ADR 0001**: `docs/adr/0001-codebase-restructure-production-vs-prototypes.md` - 重组决策记录
- **Issue #26**: https://github.com/Joeowo/PetTrip/issues/26 - 重组 ticket
- **Issue #1**: https://github.com/Joeowo/PetTrip/issues/1 - 静态目的地闭环 map

---

## 注意事项

⚠️ **这些代码仅供参考，不应直接使用**：
- 导入路径已过期（`pilot4mvp2.agent_service` → `agent_service`）
- 依赖和配置可能过时
- 新功能应在 `agent_service/` 中开发

✅ **适合的使用场景**：
- 理解演化历史
- 查看早期设计决策
- 参考实验方法和验证流程
