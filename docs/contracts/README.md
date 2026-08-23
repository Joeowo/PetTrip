# PetTrip Agent-Unity Contracts

本目录包含 PetTrip Agent 与 Unity 之间的数据契约定义。

## 契约索引

### API 契约

- **Session & Run API**: 继承自 `pilot4mvp2/agent_service`，见 issue #10 第 5 节
- **Destination Manifest API**: `GET /api/v1/destinations/{destination_id}` - 见 issue #10 第 6.1 节
- **Scene Artifact API**: `GET /api/v1/destinations/{destination_id}/scenes/{scene_id}` - 见 issue #10 第 6.2 节
- **File Download API**: `GET /api/v1/files/{file_id}` 和 `/content` - 见 issue #10 第 6.3 节

### Schema 定义

- `schemas/destination-manifest-v1.schema.json`: DestinationManifest 完整 Schema（待创建）
- `schemas/scene-artifact-v1.schema.json`: SceneArtifact 完整 Schema（待创建）
- `schemas/run-command-v1.schema.json`: Run Command 联合类型 Schema（待创建）

### 坐标约定

**Agent 内部坐标系**: `pixel_top_left`
- 原点：左上角
- X 轴：向右
- Y 轴：向下
- 单位：整数像素

**Unity 公开坐标系**: `pixel_bottom_left`
- 原点：左下角
- X 轴：向右
- Y 轴：向上
- 转换公式：`center_y_bottom_px = canvas_height_px - 1 - center_y_top_px`

服务端只负责一次转换，Unity 不得再次翻转 y。

## 版本追溯模型

见 issue #9 和 issue #10 第 4.4 节：

- **spec_version**: 目的地规格版本（首阶段固定为 1）
- **artifact_version**: 场景产物版本（首阶段固定为 1）
- **delivery_revision**: Unity 可观察的业务变化计数（单调递增）

首阶段不实现 content_version、staging/published 或共建草稿。

## 参考

- 领域语言：`CONTEXT.md`
- 实现规格：issue #10
- 架构决策：`docs/adr/`
