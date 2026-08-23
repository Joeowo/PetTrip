# 黑圈检测算法参考实现

## 算法概述

**算法名称**：black-filled-ellipse/v1

**用途**：从场景定位参考图中检测黑色填充的椭圆定位标记，用于确定宠物交互区域的计划放置中心。

**原型验证成功率**：16/16 (100%)

**原型来源**：Issue #12 - 原型验证双场景共享环境与黑圈定位链路

**关键特性**：
- 两阶段硬门槛筛选（几何资格 → 颜色资格）
- 相对环境母图的变暗检测
- 多维度加权评分与稳定 tie-break
- 8-连通分量检测 + 椭圆 mask 统计

---

## 算法详细步骤

### 第一阶段：8-连通分量检测

**目标**：从定位参考图中提取所有黑色候选区域

**步骤**：
1. 遍历定位图的所有像素，提取 `max(R, G, B) <= candidate_m_max` 的像素点
   - 默认 `candidate_m_max = 20`（极深黑色）
2. 对黑色像素进行 8-连通分量分组
   - 使用 flood-fill 算法，相邻像素（包括对角）归为同一组件
3. 得到若干个候选黑色组件

**输出**：黑色组件列表（每个组件是像素坐标集合）

---

### 第二阶段：几何资格筛选

**目标**：过滤掉明显不是定位标记的组件

**几何约束**（全部必须满足）：
1. **面积** >= `minimum_area`（默认 500 像素²）
2. **bbox 最小边** >= `minimum_bbox_side`（默认 20 像素）
3. **长宽比**：`aspect_min <= 1/轴比 <= aspect_max`（默认 0.25 - 4.0）
4. **填充率**：`fill_min <= area/bbox_area <= fill_max`（默认 0.70 - 0.90）
5. **边缘约束**：`reject_canvas_edge = true` 时不能触碰画布边缘

**计算**：
- `bbox` = 组件外接矩形
- `aspect` = max(width, height) / min(width, height)
- `fill` = 组件面积 / bbox 面积

**输出**：通过几何资格的候选组件列表

---

### 第三阶段：椭圆 Mask 构建与颜色资格筛选

**目标**：在通过几何筛选的组件上进行精细的颜色和亮度验证

#### 3.1 椭圆 Mask 构建

对每个候选组件：
1. 计算 bbox 中心 `(center_x, center_y)` 和半径 `(radius_x, radius_y)`
2. 在 bbox 内构建内接椭圆 mask：
   ```
   normalized = ((x - center_x) / radius_x)² + ((y - center_y) / radius_y)² <= 1
   ```
3. 提取椭圆内的所有像素点作为分析样本

#### 3.2 颜色特征计算

在椭圆 mask 内的像素上计算：
- `marker_max[i]` = max(R, G, B) at pixel i
- `chroma[i]` = max(R, G, B) - min(R, G, B) at pixel i
- `locator_luminance[i]` = 0.2126×R + 0.7152×G + 0.0722×B（Rec.709）
- `environment_luminance[i]` = 同上，但来自原始环境母图

#### 3.3 统计指标

- `m_p90` = marker_max 的 90 分位数（黑色深度）
- `chroma_p90` = chroma 的 90 分位数（色偏）
- `q20` = marker_max <= 20 的像素占比（黑色覆盖均匀度）
- `delta_y_mean` = 平均亮度差（environment - locator）（相对变暗程度）
- `luminance_iqr` = 亮度四分位距（内部均匀性）

#### 3.4 颜色资格硬门槛（全部必须满足）

1. **黑色深度**：`m_p90 <= ellipse_m_p90_max`（默认 12）
2. **低色偏**：`chroma_p90 <= ellipse_chroma_p90_max`（默认 6）
3. **黑色覆盖**：`q20 >= ellipse_q20_min`（默认 0.94）
4. **相对变暗**：`delta_y_mean >= delta_y_mean_min`（默认 40）
5. **椭圆覆盖**：`0.70 <= 组件面积/椭圆面积 <= 1.15`

**输出**：通过颜色资格的合格候选列表

---

### 第四阶段：多维度评分与选择

**目标**：从多个合格候选中选择最佳标记

#### 4.1 评分公式

```
score = 0.25 × blackness
      + 0.25 × coverage
      + 0.15 × neutrality
      + 0.10 × uniformity
      + 0.15 × darkening
      + 0.10 × solidity
```

其中：
- `blackness` = max(0, min(1, (20 - m_p90) / 12))
- `coverage` = max(0, min(1, (q20 - 0.80) / 0.18))
- `neutrality` = max(0, min(1, (10 - chroma_p90) / 8))
- `uniformity` = max(0, min(1, (8 - luminance_iqr) / 6))
- `darkening` = max(0, min(1, (delta_y_mean - 20) / 50))
- `solidity` = max(0, min(1, (fill - 0.55) / 0.22))

#### 4.2 Tie-break 排序

如果多个候选评分相同，按以下顺序排序：
1. `score` 降序（主排序键）
2. `fraction_max_channel_le_20` 降序
3. `max_channel_p90` 升序
4. `area` 降序
5. `bbox[1]` (top) 升序
6. `bbox[0]` (left) 升序
7. `bbox[3]` (bottom) 升序
8. `bbox[2]` (right) 升序

#### 4.3 中心坐标确定

选中候选的椭圆中心 `(center_x, center_y)` 使用 half-up 舍入为整数：
```
planned_locator_center = [half_up(center_x), half_up(center_y)]
```

其中 `half_up(x) = floor(x + 0.5)`

**输出**：最佳候选的整数中心坐标

---

## 关键参数配置

### 默认参数（DEFAULT_LOCATOR_POLICY）

| 参数名称 | 默认值 | 说明 |
|---------|--------|------|
| `algorithm` | "black-filled-ellipse/v1" | 算法版本标识 |
| `candidate_m_max` | 20 | 候选像素 max(R,G,B) 上限 |
| `minimum_area` | 500 | 最小组件面积（像素²） |
| `minimum_bbox_side` | 20 | bbox 最小边（像素） |
| `aspect_min` | 0.25 | 长宽比下限（允许 1:4 扁平） |
| `aspect_max` | 4.0 | 长宽比上限（允许 4:1 狭长） |
| `fill_min` | 0.70 | 填充率下限 |
| `fill_max` | 0.90 | 填充率上限 |
| `reject_canvas_edge` | true | 拒绝触碰边缘的候选 |
| `ellipse_m_p90_max` | 12 | 椭圆内黑色深度上限 |
| `ellipse_chroma_p90_max` | 6 | 椭圆内色偏上限 |
| `ellipse_q20_min` | 0.94 | 椭圆内黑色覆盖下限 |
| `delta_y_mean_min` | 40 | 相对环境变暗下限 |

---

## 失败边界

算法在以下情况拒绝并抛出异常：

1. **无候选通过几何资格** → `"no_plausible_black_marker"` + 拒绝原因统计
2. **无候选通过颜色资格** → 同上
3. **尺寸不符** → `"dimension_mismatch"` (定位图与环境母图尺寸不同)

**注意**：多个合格候选时算法选择最高分，不视为失败（通过 tie-break 保证确定性）。

---

## 原型代码引用

**文件路径**：`.claude/worktrees/issue12-experiment-prototype/pilot4mvp3/scripts/issue12_controller.py`

**主函数**：`detect_black_locator(environment_path, locator_path, policy)` (lines 101-283)

**辅助函数**：
- `_percentile(values, fraction)` (lines 88-98) - 百分位数计算
- `half_up(value)` (lines 67-68) - 半数进位舍入
- `DEFAULT_LOCATOR_POLICY` (lines 71-85) - 默认参数

**返回结构**：
```python
{
    "algorithm": "black-filled-ellipse/v1",
    "policy": {...},  # 完整参数配置
    "raw_component_count": int,  # 初始组件数
    "qualified_candidate_count": int,  # 合格候选数
    "rejection_counts": {...},  # 拒绝原因统计
    "selected_candidate": {...},  # 选中候选详细信息
    "candidate_diagnostics": [...],  # 前 20 个候选诊断
    "planned_locator_center_float": [float, float],
    "planned_locator_center": [int, int],  # 最终整数坐标
    "bbox": [left, top, right, bottom],
    "area": int
}
```

---

## 验证结果

### Issue #12 原型验证

**测试覆盖**：
- 4 个不同环境概念 (G02, G03, G06, G08)
- 2 条母图路线 (M0: style only, M1: style + character)
- 每环境 2 个场景 (A, B)
- 共计 **16 个定位调用**

**成功率**：16/16 (100%)
- 16/16 locator 唯一识别合格黑色标记
- 16/16 planned_locator_center 稳定计算为整数坐标
- 16/16 aperture 位置通过人工验收

**证据路径**：
- 完整运行：`.claude/worktrees/issue12-experiment-prototype/pilot4mvp3/issue12/runs/issue12-full-001/`
- 恢复验证：`.claude/worktrees/issue12-experiment-prototype/pilot4mvp3/issue12/runs/issue12-recovery-001/`
- 分支：`worktree-issue12-experiment-prototype`
- 提交：`1dee5bd` (chore(issue12): record accepted prototype review)

**回归保证**：
该结论是原型数据集的回归验收，不宣称对所有未见图片天然达到 100%。对未见 locator 数据保留 fail-closed 与人工复核机制（参见 `dual-scene-generation-protocol-v0.1.md` 第 3 节）。

---

## T6 实施建议

### 复用策略

1. **核心逻辑复用**：
   - 两阶段硬门槛筛选逻辑
   - 椭圆 mask 构建和统计方法
   - 评分公式和 tie-break 排序

2. **参数可配置化**：
   - 保留 `DEFAULT_LOCATOR_POLICY` 作为基线
   - 允许通过 `policy` 参数动态调整阈值
   - 生产环境可根据实际数据微调

3. **错误处理**：
   - 检测失败时返回结构化错误（`rejection_counts`）
   - 保留 `candidate_diagnostics` 用于调试
   - 最多 3 attempts 重试（参见 Issue #18 规格）

### 性能优化

1. **候选预筛选**：在 8-连通分量阶段提前拒绝极小组件
2. **椭圆 mask 缓存**：相同 bbox 的椭圆 mask 可复用
3. **并行化**：多个 locator 的检测可并行处理

### 鲁棒性增强

1. **光照适应**：如遇光照变化导致失败，可动态调整 `candidate_m_max`
2. **形变容忍**：`aspect_min/max` 范围已较宽，可根据实际需要放宽
3. **诊断输出**：保留完整的 `candidate_diagnostics` 用于失败分析

### 测试覆盖

参考 Issue #18 测试要求：
1. 恰好一个合法黑圈时检测得到稳定整数圆心 ✅（原型已验证）
2. 无圆时拒绝（技术失败）
3. 多圆时选择最佳（通过评分）
4. NaN/无限值时拒绝（代码中无浮点运算发散风险）
5. 尺寸不符时拒绝 ✅（原型已实现）
6. 圆心超出画布时在后续 aperture 绘制阶段检测

---

## 与协议文档的关系

本算法是 `dual-scene-generation-protocol-v0.1.md` 第 3 节"scan_planned_center"步骤的具体实现。

**协议边界**：
- **输入**：environment_image + locator_image
- **输出**：planned_locator_center (整数坐标)
- **失败边界**：无候选通过资格 / 多候选评分完全相同（极端情况，原型未遇到）
- **确定性保证**：给定相同输入，输出稳定（评分和 tie-break 均确定性）

**与后续步骤的关系**：
- planned_locator_center 传递给第 4 步 `draw_deterministic_aperture`
- aperture 的 center/radius 用于构建 InteractionZone
- **但 aperture 不等于最终点击真值**，最终点击区域仍需对最终图独立检测

---

## 相关 Issue 和文档

- **Issue #12**：原型验证双场景共享环境与黑圈定位链路（已完成）
- **Issue #18**：T6 场景定位与圆检测（待实施）
- **Issue #4**：验证双场景共享环境生产路线（wayfinder）
- **dual-scene-generation-protocol-v0.1.md**：完整五步生成链路协议

---

## 变更历史

- **2026-08-23**：创建文档（Issue #36 - 3.2）
- **2026-08-23**：重写为完整算法参考（对照原型代码 issue12_controller.py）
