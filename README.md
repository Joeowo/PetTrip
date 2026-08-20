# 双场景共享环境生产路线验证原型

**状态**: PROTOTYPE - 用于技术验证，非生产代码  
**相关工单**: [#4 验证双场景共享环境生产路线](https://github.com/Joeowo/PetTrip/issues/4)  
**创建时间**: 2026-08-20

## 问题陈述

PetTrip 首个默认目的地需要生成**两个场景**，它们共享同一个视觉环境（背景、地标、光照、画风），但宠物处于不同的行为和状态。

**核心问题**: 哪条视觉生产路线能在产品可接受的一致性水平下实现"共享环境"？

## 原型产物

本原型包含以下文件：

1. **`dual-scene-shared-env-validation-spec.md`** - 完整实验规格
   - 定义一致性指标（地标位置、光照、画风等）
   - 定义四条候选生产路线
   - 定义成功判定条件

2. **`experiment_runner.py`** - 实验执行脚本
   - 自动运行四条路线的图片生成
   - 保存所有中间产物和元数据
   - 输出实验汇总 JSON

3. **`review-guidelines.md`** - 人工评审指南
   - 六个评审维度的详细说明（1-5 分制）
   - 自动测量指标（地标偏移、SSIM、色板漂移）
   - 评审表格式和失败分类

4. **`README.md`** - 本文件

## 四条候选路线

### 路线 A: 纯环境母版 + 局部宠物编辑
1. 生成无宠物的纯环境图
2. 在母版上 inpaint 宠物状态1 → 场景 A
3. 在同一母版上 inpaint 宠物状态2 → 场景 B

**假设**: 母版保证环境一致性  
**风险**: inpainting 可能改变 Mask 外背景

### 路线 B: 完整场景 A 作为参考生成场景 B
1. 生成完整场景 A（环境 + 宠物状态1）
2. 使用场景 A 作为参考图生成场景 B（宠物状态2）

**假设**: 参考图约束保持环境一致  
**风险**: 可能过度保留宠物状态1 或过度重绘

### 路线 C: 模型定位 + 程序化 Mask + 独立角色生成
1. 生成纯环境母版
2. 让 image-2 生成带 Mask 的图（定位位置1，Mask 尺寸可能不准）
3. 确定性程序计算 Mask 中心坐标和直径
4. 确定性程序在原始母版上重新绘制固定尺寸 Mask
5. image-2 依据程序 Mask 生成宠物状态1 → 场景 A
6. 重复步骤 2-5 用于位置2 → 场景 B

**假设**: 模型语义定位准确（pilot4mvp3 验证）  
**风险**: image-2 对程序 Mask 的角色生成服从性未单独验证

### 路线 D: 双场景同 Prompt 批次生成
1. 用强一致性 Prompt 独立生成场景 A
2. 用强一致性 Prompt 独立生成场景 B

**假设**: Prompt 语义约束能引导一致性  
**风险**: 没有显式共享母版，完全依赖模型理解

## 使用方法

### 前置条件

1. Python 3.10+
2. 安装依赖（pilot4mvp2 的 requirements.txt）
3. 配置图片生成 API 端点和密钥

### 运行实验

```bash
# 1. 进入原型目录
cd .claude/worktrees/prototype-shared-env

# 2. 配置 API 密钥（示例）
export OPENAI_API_KEY="your-api-key"

# 3. 运行实验脚本
python experiment_runner.py
```

### 输出结构

```
outputs/
├── C01-RA/
│   ├── pure-env.png          # 纯环境母版
│   ├── SceneA.png            # 场景 A（宠物状态1）
│   ├── SceneB.png            # 场景 B（宠物状态2）
│   ├── prompts.json          # 使用的 Prompt
│   └── run.json              # 运行元数据
├── C01-RB/
│   ├── SceneA.png
│   ├── SceneB.png
│   ├── prompts.json
│   └── run.json
├── C01-RC/                   # 跳过，记录原因
├── C01-RD/
│   ├── SceneA.png
│   ├── SceneB.png
│   ├── prompts.json
│   └── run.json
├── C02-RA/
│   └── ...
├── ...
└── experiment-summary.json   # 汇总
```

### 人工评审

1. 打开 `review-guidelines.md`，理解六个评审维度
2. 使用图片查看器并排对比每组的 SceneA 和 SceneB
3. 按照评审表格式填写分数（1-5 分）
4. 记录失败原因（如适用）
5. 计算每条路线的成功率（4 个概念中至少 3 个均分 ≥ 4）

### 自动测量（可选）

实现以下脚本来计算自动指标：

- `measure_landmark_drift.py` - 地标位置偏移
- `measure_ssim.py` - 结构相似度
- `measure_color_drift.py` - 色板漂移

## 一致性指标定义

### 环境一致性

**包含**:
- 同一地标（灯塔、桥、树）保持相同的身份、位置、形状、视角
- 同一光照条件（时间、天气、主光方向、色温）
- 同一画风、色板、材质处理
- 同一构图框架（地平线、前中远景分层）

**允许变化**:
- 宠物位置、姿态、动作
- 与宠物互动相关的局部细节
- 动态元素的微小变化（云、波浪）

### 成功判定

**单个概念成功**: 六个评审维度均分 ≥ 4 分  
**路线整体通过**: 4 个概念中至少 3 个成功

## 预期结论

本原型的目标是回答：

1. **哪条路线最有希望？** - 成功率最高的路线
2. **一致性的瓶颈在哪？** - 最常失败的维度（地标、光照、画风）
3. **需要什么降级方案？** - 如果所有路线都失败，是否需要改为"相似风格"而非"共享环境"
4. **具体失败模式是什么？** - 帮助后续技术改进

## 与已有验证的关系

- **pilot4mvp3/mask-stability-validation-spec.md** - 已证明路线 C 的 Mask 不稳定
- **docs/scene-generation-quality-validation-spec.md** - 单场景高质量生成，本原型聚焦双场景一致性
- **Session 0** - 已确认 image-2 的基础能力

## Prototype 边界

本原型**只验证技术可行性**，不包含：

- ❌ 生产级编排、重试、并发
- ❌ LLM 自动规划宠物位置
- ❌ Unity 坐标转换
- ❌ 视频生成
- ❌ 大规模统计验证
- ✅ 四条路线的真实样本生成
- ✅ 一致性指标的定义和测量方法
- ✅ 人工评审流程和判定标准

## 下一步

根据实验结果：

1. **如果路线 A 或 B 通过** → 进入 `/to-spec`，制定生产规格
2. **如果路线 D 通过** → 评估是否需要额外的一致性约束机制
3. **如果所有路线失败** → 考虑降级方案：
   - 使用"相似风格"而非"共享环境"
   - 只生成一个场景，宠物状态用 Unity 切换
   - 探索其他技术路线（如 ControlNet、视频模型）

## 已知限制

1. **当前 image_provider 只支持 text-to-image** - 路线 A 和 B 的 inpainting/参考图功能用 Prompt 模拟
2. **样本量较小** - 每条路线只有 4 个概念，不足以进行统计推断
3. **人工评审的主观性** - 需要三名独立评审取中位数

## 相关文档

- [工单 #4](https://github.com/Joeowo/PetTrip/issues/4) - 验证双场景共享环境生产路线
- [工单 #1](https://github.com/Joeowo/PetTrip/issues/1) - 规划首个默认目的地静态纵向闭环（父地图）
- `pilot4mvp3/mask-stability-validation-spec.md` - Mask 稳定性验证（路线 C 的负向证据）
- `docs/scene-generation-quality-validation-spec.md` - 单场景质量验证规格
