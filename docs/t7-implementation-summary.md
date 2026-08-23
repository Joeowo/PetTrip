# T7 场景生成工作流实施总结

**任务**: Issue #19 - T7: Mask 生成与场景最终生成  
**状态**: ✅ 完成  
**日期**: 2026-08-16  
**分支**: `worktree-feat-t7-integration`

---

## 概述

T7 实现了从共享环境母图到最终场景图的完整生成链路，包括 Mask 生成、黑色圆打洞、以及真实图片生成 Provider 集成。工作流支持 mock 和真实两种模式，具备完整的错误处理、重试机制和幂等性保护。

---

## 核心功能

### 1. Mask 生成与打洞 ✅

**功能**：
- 生成二值 Mask（圆内白色 255，圆外黑色 0）
- 生成 aperture 图（环境母图 + 黑色实心圆）
- 支持边界检查（圆不能超出画布）
- 字节稳定性（相同输入总是生成相同的字节）

**实现**：
- `agent_service/domain/mask_generation.py`
- `generate_mask_and_aperture()` 函数
- 使用 PIL 确定性绘制

**测试**：16/16 通过
- 字节稳定性
- 输入变化输出变化
- 边界检查
- 坐标系统验证
- 几何参数一致性

---

### 2. 场景生成工作流 ✅

**流程**：
```
1. ensure_shared_environment → 加载共享环境母图
2. generate_localization_reference → 生成定位参考（T6，此处为 no-op）
3. detect_interaction_circle → 验证圆心（边界检查）
4. build_generation_mask → 生成 Mask 和 aperture
5. generate_final_scene → 生成最终场景（mock 或真实 Provider）
6. validate_scene_artifact → 验证场景制品
7. should_retry → 决策：提交 | 重试 | 失败
8. commit_scene_artifact → 原子提交到数据库
```

**重试机制**：
- 最多 3 attempts（可配置）
- 只重试最终场景生成
- Mask、aperture、环境母图不变
- 保持原始错误信息用于决策

**实现**：
- `agent_service/workflows/scene_generation.py`
- 使用 LangGraph 构建状态机
- 每个节点独立可测试

**测试**：8/8 通过
- 端到端工作流
- 原子提交
- 重试逻辑
- 内部资产隔离
- Mask 与 InteractionZone 一致性
- 边界检查
- 坐标系统
- 可见性控制

---

### 3. 真实 Provider 集成 ✅

#### 3.1 图像编辑 Provider

**同步 API**（回退模式）：
- 扩展 `OpenAICompatibleImageProvider`
- 实现 `edit()` 方法
- 使用 `/images/edits` 端点
- 支持 multipart/form-data 上传

**实现**：
- `agent_service/adapters/image.py`
- `ImageEditRequest` 数据类
- `ImageResult` 返回格式

#### 3.2 异步任务 API（推荐模式）

**功能**：
- 异步任务提交（POST /v1/tasks）
- 短连接轮询（不受网关超时影响）
- 幂等键支持（重试不重复扣费）
- 结构化错误信息

**生命周期**：
```
submit_task() → poll_until_complete() → download_result()
    ↓               ↓                         ↓
 task_id      pending/running           ImageResult
              /completed/failed
```

**实现**：
- `agent_service/adapters/async_image_task.py`
- `AsyncImageTaskClient` 类
- 基于 pilot4mvp2/relay_async_image.py 设计

**测试**：5/5 通过
- 提交任务成功
- 幂等冲突处理（409）
- 轮询直到完成
- 任务失败处理
- 幂等键生成

#### 3.3 场景生成集成

**提示词构建**：
```python
def build_scene_generation_prompt(pet_behavior: str, pet_emotion: str) -> str:
    return (
        f"Replace the black circle with a cute pet character. "
        f"The pet should be {pet_behavior}, showing {pet_emotion} emotion. "
        f"Keep the surrounding environment unchanged. "
        f"The pet should fit naturally within the circular area."
    )
```

**幂等键生成**：
```python
def generate_idempotency_key(
    scene_id: str,
    aperture_sha256: str,
    pet_behavior: str,
    pet_emotion: str,
) -> str:
    content = f"{scene_id}:{aperture_sha256}:{pet_behavior}:{pet_emotion}"
    hash_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"scene-{scene_id[:8]}-{hash_digest[:16]}"
```

**混合策略**：
- 检查 `config.image_use_async_tasks` 标志
- 优先使用异步任务 API（更可靠）
- 回退到同步 edit API（兼容性）

**实现**：
- `agent_service/domain/scene_image_generation.py`
- `generate_final_scene_with_provider()` 异步版本
- `generate_final_scene_sync()` 同步包装

---

## 数据模型

### SceneArtifact

最终场景的原子制品，包含：

```python
{
    "scene_artifact_id": str,          # 唯一 ID
    "destination_id": str,             # 所属目的地
    "scene_id": str,                   # 所属场景
    "spec_id": str,                    # 规格 ID
    "render_file_id": str,             # 最终场景图文件 ID
    "render_sha256": str,              # 最终场景图 SHA256
    "interaction_zone_id": str,        # 交互区域 ID
    "shared_environment_sha256": str,  # 共享环境哈希（验证）
    "prompt_snapshot_id": str | None,  # 提示词快照 ID
    "created_at": datetime,            # 创建时间
}
```

### InteractionZone

交互热区定义：

```python
{
    "interaction_zone_id": str,        # 唯一 ID
    "destination_id": str,             # 所属目的地
    "scene_id": str,                   # 所属场景
    "center_x_px": int,                # 圆心 X（pixel_top_left）
    "center_y_px": int,                # 圆心 Y（pixel_top_left）
    "radius_px": int,                  # 半径（像素）
    "coordinate_space": str,           # 坐标系统（"pixel_top_left"）
    "created_at": datetime,            # 创建时间
}
```

---

## 配置说明

### 环境变量

启用异步任务 API：
```bash
IMAGE_USE_ASYNC_TASKS=true
IMAGES_BASE_URL=https://api.provider.com
IMAGES_API_KEY=your-api-key
IMAGES_MODEL=gpt-image-2
IMAGE_TIMEOUT=600
```

回退到同步 API（不设置 `IMAGE_USE_ASYNC_TASKS`）：
```bash
IMAGES_BASE_URL=https://api.provider.com
IMAGES_API_KEY=your-api-key
IMAGES_MODEL=gpt-image-2
IMAGE_TIMEOUT=120
```

### 代码配置

```python
from agent_service.shared.config import load_settings
from agent_service.workflows.scene_generation import run_scene_generation_workflow

config = load_settings()

final_state = run_scene_generation_workflow(
    destination_id="dest-123",
    scene_id="scene-456",
    spec_id="spec-789",
    shared_environment_id="env-abc",
    semantic_anchor="木屋前的空地",
    pet_behavior="四处张望",
    pet_emotion="好奇",
    planned_center_x=1024,
    planned_center_y=576,
    interaction_diameter_px=160,
    repo=repo,
    file_storage=file_storage,
    use_mock_final_scene=False,  # 使用真实 Provider
    storage=storage,
    config=config,                # 传入配置
)
```

---

## 测试结果

### 单元测试

- **Mask 生成**: 16/16 ✅
- **场景生成工作流**: 8/8 ✅
- **异步任务客户端**: 5/5 ✅
- **总计**: 29/29 ✅

### 测试覆盖率

- Mask 生成：边界检查、字节稳定性、几何一致性
- 工作流：端到端、重试、错误处理、原子提交
- Provider：提交、轮询、下载、幂等性、错误处理

---

## 文件清单

### 核心实现

```
agent_service/
├── domain/
│   ├── mask_generation.py              # Mask 生成核心逻辑
│   └── scene_image_generation.py       # 场景图片生成封装
├── adapters/
│   ├── image.py                        # 图像 Provider（同步 edit）
│   └── async_image_task.py            # 异步任务客户端
├── workflows/
│   └── scene_generation.py            # 场景生成工作流
└── storage/
    └── destination_storage.py          # Repository（新增方法）
```

### 测试

```
agent_service/tests/
├── domain/
│   ├── test_mask_generation.py         # Mask 生成测试（16个）
│   └── test_scene_image_generation.py  # Provider 集成测试
├── adapters/
│   └── test_async_image_task.py        # 异步任务测试（5个）
├── workflows/
│   └── test_scene_generation.py        # 工作流集成测试（8个）
└── helpers/
    └── simple_file_storage.py          # 测试辅助类
```

---

## 技术亮点

### 1. 字节稳定性

相同输入总是生成相同的字节：
- PNG 压缩参数固定
- 整数坐标和半径
- 确定性绘制顺序
- 不依赖随机数或时间戳

### 2. 幂等性保护

重试安全，不重复扣费：
- 基于场景 ID + aperture 哈希生成幂等键
- 相同输入总是生成相同的键
- 支持 409 冲突处理
- 网络故障可安全重试

### 3. 错误传播

保持原始错误用于决策：
- 验证节点不覆盖已有错误
- 最终场景生成节点不覆盖已有错误
- 重试逻辑基于真实失败原因

### 4. 混合策略

灵活的 Provider 选择：
- 异步优先（更可靠）
- 同步回退（兼容性）
- mock 模式（测试）

### 5. 原子提交

一次性提交所有制品：
- SceneArtifact
- InteractionZone
- 文件注册
- 避免部分成功状态

---

## 使用示例

### Mock 模式（测试）

```python
final_state = run_scene_generation_workflow(
    destination_id="dest-123",
    scene_id="scene-456",
    spec_id="spec-789",
    shared_environment_id="env-abc",
    semantic_anchor="木屋前的空地",
    pet_behavior="四处张望",
    pet_emotion="好奇",
    planned_center_x=1024,
    planned_center_y=576,
    interaction_diameter_px=160,
    repo=repo,
    file_storage=file_storage,
    use_mock_final_scene=True,  # Mock 模式
    storage=storage,
)

if final_state["error"] is None:
    print(f"✅ 场景生成成功: {final_state['scene_artifact_id']}")
else:
    print(f"❌ 场景生成失败: {final_state['error']}")
```

### 真实模式（生产）

```python
from agent_service.shared.config import load_settings

config = load_settings()

final_state = run_scene_generation_workflow(
    destination_id="dest-123",
    scene_id="scene-456",
    spec_id="spec-789",
    shared_environment_id="env-abc",
    semantic_anchor="木屋前的空地",
    pet_behavior="四处张望",
    pet_emotion="好奇",
    planned_center_x=1024,
    planned_center_y=576,
    interaction_diameter_px=160,
    repo=repo,
    file_storage=file_storage,
    use_mock_final_scene=False,  # 真实模式
    storage=storage,
    config=config,
)

if final_state["error"] is None:
    artifact = repo.get_scene_artifact(final_state["scene_artifact_id"])
    zone = repo.get_interaction_zone(artifact["interaction_zone_id"])
    print(f"✅ 场景生成成功")
    print(f"  - 最终场景: {artifact['render_file_id']}")
    print(f"  - 交互区域: ({zone['center_x_px']}, {zone['center_y_px']}) r={zone['radius_px']}")
else:
    print(f"❌ 场景生成失败: {final_state['error']}")
    print(f"  - 尝试次数: {final_state['scene_generation_attempt']}")
```

---

## 已知限制

### 1. 同步 edit API 超时

- 某些提供商的同步 API 受网关超时限制
- 推荐使用异步任务 API
- 回退到同步 API 仅用于兼容性

### 2. 提示词语言

- 当前提示词为英文
- 中文宠物行为/情绪需要翻译或使用中文提示词

### 3. 重试策略

- 当前最多重试 3 次
- 不区分暂时性故障和永久性故障
- 未来可添加指数退避和智能重试

### 4. 性能监控

- 缺少生成时间、成本追踪
- 未来可添加 Prometheus 指标

---

## 下一步

### 短期（1-2 周）

1. **真实环境端到端测试**
   - 使用真实 Provider 生成场景
   - 验证图像质量和宠物位置
   - 收集性能数据

2. **监控和日志**
   - 添加结构化日志
   - 记录生成时间、重试次数
   - 错误聚合和告警

3. **提示词优化**
   - 支持中文提示词
   - 根据宠物类型调整提示
   - A/B 测试不同提示词

### 中期（1-2 个月）

1. **质量验证**
   - 宠物检测（确保宠物出现）
   - 环境一致性检查
   - 自动化质量评分

2. **性能优化**
   - 并行处理多个场景
   - 缓存 aperture 图
   - 优化轮询间隔

3. **成本控制**
   - 记录每次生成的成本
   - 实现成本配额
   - 优化提示词以降低成本

### 长期（3+ 个月）

1. **高级功能**
   - 支持多宠物场景
   - 动态调整交互区域大小
   - 风格迁移和一致性

2. **架构升级**
   - 迁移到消息队列（异步处理）
   - 分布式任务调度
   - 多提供商负载均衡

---

## 参考资料

- **Issue #19**: T7: Mask 生成与场景最终生成
- **PR #42**: fix(T7): 集成测试修复与工作流完善
- **协议文档**: docs/contracts/dual-scene-generation-protocol-v0.1.md
- **原型验证**: pilot4mvp2/relay_async_image.py
- **Issue #12**: 原型验证双场景共享环境与黑圈定位链路

---

## 贡献者

- **实施**: Claude Fable 5
- **审查**: 待定
- **测试**: 自动化测试 + 人工验收

---

**完成日期**: 2026-08-16  
**最后更新**: 2026-08-16
