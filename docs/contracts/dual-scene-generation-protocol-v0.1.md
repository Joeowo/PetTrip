# 双场景共享环境生成协议 v0.1

**Status**: Draft  
**Validated by**: [Issue 12 原型验证](https://github.com/Joeowo/PetTrip/issues/12)  
**Referenced by**: Issue 10 (统一目的地数据模型与 Unity 交付契约)  
**Last updated**: 2026-08-23

---

## 概述

本协议定义了"同一目的地的两个场景共享不可变环境母图、通过黑色定位标记确定宠物放置中心、生成最终静态场景"的完整生成链路。

该路线已在 Issue 12 原型实验中通过人工评审：4 个目的地 × 2 条母图路线 × 2 个场景 = 16 条最终场景分支全部技术成功，16/16 locator 唯一识别，16/16 aperture 位置正确，16/16 最终场景通过人工验收。

**关键边界**：本协议定义的 `planned_locator_center` 和 `deterministic_aperture` 仍然只是计划放置位置，**不等于最终交付图中的点击真值**。最终点击区域仍需对最终宠物图执行独立检测或分割。

---

## 五步生成链路

### 1. environment_base

**目标**：生成同一 destination/route 的 A、B 场景共享的不可变环境母图。

**输入**：

- `destination_requirement`：场景要求（地标、构图、光照）
- `style_reference`：画风参考图（从画风素材库固定单元格提取）
- `composition_prompt`：构图提示词
- `character_reference`（可选，仅 M1 路线）：宠物室外三视图下半张，用于理解尺度和预留活动空间
- `generation_boundary`：固定生成边界，禁止宠物、黑圈、UI、文字、水印等泄漏

**输出**：

- `environment_image`：2048 * 1152 纯环境母图，包含两个语义明确、空间分离、可站立、避开边缘与主地标的空环境锚点
- `image_sha256`：母图 SHA-256
- `dimensions`：`[width, height]`

---

### 2. semantic_locator

**目标**：基于不可变环境母图，仅在指定锚点的可站立位置绘制一个黑色实心圆或椭圆标记，不生成宠物，不重新构图。

**输入**：

- `environment_image`（作为参考图）
- `anchor_description`：例如"木屋前道路内侧平地"
- `locator_prompt`：要求模型仅在该锚点绘制黑色实心圆，禁止宠物、禁止修改环境

**输出**：

- `locator_image`：语义定位图
- `image_sha256`：定位图 SHA-256

**失败边界**：

- 无黑色标记
- 多个黑色标记无法唯一选择
- 环境发生显著变化
- 标记不在可站立区域或触碰画布边缘
- 网络失败、API 超时、submission_unknown

---

### 3. scan_planned_center

**目标**：程序从 semantic_locator 图确定性选择唯一合格黑色标记并得到整数中心坐标。

**输入**：

- `environment_image`（用于计算相对变暗）
- `locator_image`

**算法**：`black-filled-ellipse/v1`

**检测策略**（两阶段硬门槛）：

1. **几何资格先行**：
   - 8-连通组件候选：`max(R, G, B) <= candidate_m_max`（默认 20）
   - 面积 `>= minimum_area`（默认 500）
   - bbox 最小边 `>= minimum_bbox_side`（默认 20）
   - 轴比 `aspect_min <= w/h <= aspect_max`（默认 0.25–4.0）
   - 填充率 `fill_min <= area/bbox_area <= fill_max`（默认 0.70–0.90）
   - 不触碰画布边缘（`reject_canvas_edge = true`）
   - bbox 内接椭圆 mask 非空（极小组件 fallback）

2. **颜色资格随后**（在内接椭圆 mask 内统计）：
   - 黑色深度：`max_channel_p90 <= ellipse_m_p90_max`（默认 12）
   - 低色偏：`chroma_p90 <= ellipse_chroma_p90_max`（默认 6）
   - 内部黑色覆盖均匀度：`fraction_max_channel_le_20 >= ellipse_q20_min`（默认 0.94）
   - 相对母图显著变暗：`delta_luminance_mean >= delta_y_mean_min`（默认 40）

**评分与选择**：

- 只有通过全部硬门槛的候选才能进入评分
- score 大致为：
  ```
  0.25 × blackness
  + 0.25 × coverage
  + 0.15 × neutrality
  + 0.10 × uniformity
  + 0.15 × darkening
  + 0.10 × solidity
  ```
- 多个合格候选时选最高分，并使用稳定 tie-break（bbox 面积、y 坐标、x 坐标）
- **score 只用于定位标记选择，不是最终图片质量评分**

**输出**：

- `planned_locator_center`：`[x: int, y: int]`，在 environment 坐标空间
- `detection_diagnostics`：算法版本、候选数、拒绝原因分布、选中候选的形状/颜色/评分

**失败边界**：

- 无候选通过几何资格
- 无候选通过颜色资格
- 多个合格候选评分完全相同且 tie-break 失败（极端情况）

**回归保证**：Issue 12 的 16 张真实 locator 在当前策略下实现 16/16 唯一识别，但该结论是本原型数据集的回归验收，不宣称对所有未见图片天然达到 100%。对未见 locator 数据保留 fail-closed 与人工复核机制。

---

### 4. draw_deterministic_aperture

**目标**：回到原始环境母图，在 `planned_locator_center` 绘制固定黑色实心圆 aperture。

**输入**：

- `environment_image`（原始母图，不是 locator 图）
- `planned_locator_center`：`[x, y]`
- `short_edge_ratio`：默认 0.14

**几何规则**：

- `short_edge = min(environment_width, environment_height)`
- `diameter_float = short_edge × short_edge_ratio`
- `diameter_px = nearest_even(diameter_float)`（最近偶数像素）
- `radius_px = diameter_px / 2`
- 圆心 `(x, y)` 在 environment 坐标空间
- 填充颜色：`#000000`（纯黑）

**输出**：

- `aperture_image`：确定性打洞母图
- `aperture_sha256`：aperture 图 SHA-256
- `center`：`[x, y]`
- `radius`：`radius_px`
- `diameter`：`diameter_px`

**失败边界**：

- planned_center 超出画布范围
- aperture 半径导致圆形超出画布
- 文件写入失败

**确定性保证**：给定相同的 environment 和 planned_center，aperture 图字节稳定。

**与 InteractionZone 的关系**：aperture 的 `center` 和 `radius` 将被 Unity 用于构建 InteractionZone，但 **aperture 仍然只是计划放置位置，不等于最终交付图中的点击真值**。最终点击区域仍需对最终宠物图执行独立检测或分割。

---

### 5. final_pet_scene

**目标**：输入 aperture 图和宠物角色引用，生成用符合动作与尺度的宠物替换黑洞并保持洞外环境的最终静态场景。

**输入**：

- `aperture_image`（作为参考图）
- `character_bottom`：宠物室外三视图下半张（已按高度上下二分裁剪）
- `action_description`：例如"停下并抬头望向山口"
- `replacement_prompt`：要求模型用宠物替换黑洞，保持洞外环境不变

**输出**：

- `final_scene_image`：最终宠物场景
- `image_sha256`：最终图 SHA-256

**失败边界**：

- 宠物未出现或位置错误
- 洞外环境发生显著变化
- 宠物尺度或动作不符合要求
- 网络失败、API 超时、submission_unknown

**与点击真值的关系**：最终图成功交付后，Unity 需要对 `final_scene_image` 执行独立检测或分割，得到宠物的实际 bbox 或 mask，并以此构建最终的点击热区。**本协议定义的 aperture center/radius 不等于最终点击真值**。

---

## API 配置

Issue 12 原型验证中统一使用的配置（供参考，生产实现可调整）：

```json
{
  "provider": "65535",
  "model": "gpt-image-2",
  "size": "16:9",
  "resolution": "2k",
  "quality": "high",
  "n": 1,
  "timeout": 900
}
```

---

## 参考图策略

### 母图路线

- **M0**：`style_reference` only
- **M1**：`style_reference` + `character_bottom`（三视图下半张仅用于理解尺度和预留活动空间，母图不得包含宠物泄漏）

### locator 阶段

- 参考图：`route_environment`（即步骤 1 的 environment_image）

### final 阶段

- 参考图：`deterministic_aperture` + `character_bottom`

---

## 幂等性与恢复纪律

- 每个逻辑任务使用固定 idempotency key（由 destination/route/scene/phase 确定）
- 网络恢复沿用原 key 和 task ID，不重新创建任务
- task ID 获取后立即落盘
- `submission_unknown` 状态不自动重试，不换幂等键；可人工 attach task ID 或 abandon
- Prompt、参数或参考图变化必须使用新 run_id 和新幂等键

---

## 证据与审计

- 每步保存：输入引用、输出路径、SHA-256、参数、脱敏 API 元数据
- 失败分支保留：错误码、错误消息、attempt 次数
- 静态 evidence HTML 展示完整时间线，不包含密钥或签名 URL
- 签名下载 URL 必须使用独立、无 Authorization 的 Session

---

## 已知限制与后续工作

1. **点击真值仍需独立检测**：本协议定义的 planned_locator_center 和 deterministic_aperture 只是计划放置位置，不等于最终交付图中的点击真值。最终点击区域仍需对最终宠物图执行独立检测或分割。

2. **检测器回归范围**：black-filled-ellipse/v1 在 Issue 12 的 16 张真实 locator 上实现 16/16 唯一识别，但该结论是本原型数据集的回归验收，不宣称对所有未见图片天然达到 100%。对未见 locator 数据保留 fail-closed 与人工复核机制。

3. **场景一致性评估**：Issue 12 人工确认每个目的地的 A/B 场景可识别为同一共享环境，但未量化"一致性"的自动评估标准。生产实现可能需要补充客观指标（地标保留率、SSIM、色调分布等）。

4. **aperture 参数可调性**：当前 `short_edge_ratio = 0.14` 和 `nearest_even` 取整是 Issue 12 验证通过的配置。生产实现可根据宠物尺度或目的地类型动态调整，但需重新验证。

5. **非场景步骤失败处理**：本协议覆盖单场景技术链路。跨场景的重试、降级、演示兜底等策略由 Issue 10 spec 和 Issue 7 决策定义。

---

## 变更历史

- **v0.1** (2026-08-23)：从 Issue 12 原型验证提炼初版协议，覆盖五步链路、检测策略、幂等纪律和边界声明。

---

## 引用

- [Issue 12: 原型验证双场景共享环境与黑圈定位链路](https://github.com/Joeowo/PetTrip/issues/12)
- [Issue 10: 实现统一目的地数据模型与 Unity 交付契约](https://github.com/Joeowo/PetTrip/issues/10)
- [Issue 4: 验证双场景共享环境生产路线](https://github.com/Joeowo/PetTrip/issues/4)
- 原型证据：
  - `pilot4mvp3/issue12/runs/issue12-full-001/`
  - `pilot4mvp3/issue12/runs/issue12-recovery-001/`
  - 分支：`worktree-issue12-experiment-prototype`
  - 提交：`1dee5bd` (chore(issue12): record accepted prototype review)
