# PetTrip MVP 技术栈联通测试清单

这份清单用于在一天内验证 PetTrip 各技术环节能否交换真实数据。只判断
“能不能打通”，不评价画面质量，也不实现完整 MVP。

## 统一最小场景

所有测试共用一个场景，避免不同输入掩盖接口问题。

- 输入：`生成一个横向 2D 海边场景，包含一座灯塔；宠物可以在灯塔前挥手；右侧可以放置一个小窝；不要出现车辆。`
- 设计画布：`512 x 288`。图片 API 可按模型支持的尺寸生成，再由 OpenCV 缩放。
- 固定互动：`pet_wave`。
- 固定共建物：`small_shelter`。
- 固定运行 ID：`pilot-20260812-001`。
- 反例运行 ID：`pilot-20260812-invalid`。

每个环节都必须产生可检查的文件。只看到日志或界面变化不算通过。

```text
pilot4mvp/runs/pilot-20260812-001/
  input.json
  world-spec.json
  scene-plan.json
  events.jsonl
  assets/concept.png
  assets/lighthouse.png
  assets/asset-manifest.json
  scene-snapshot.json
  scene-snapshot-v2.json
  unity-screenshot.png
  validation-report.json
  versions.txt
```

## 开始前检查

先准备主链依赖，不安装今天用不到的视觉模型或基础设施。

- [ ] 使用 Python 3.12，并安装 FastAPI、Pydantic v2、OpenAI SDK、Pillow、
  OpenCV 和 JSON Schema 校验库。
- [ ] 设置 `OPENAI_API_KEY`；密钥不得写入日志或产物。
- [ ] 显式配置当前账号可用的 Responses 模型。技术路线没有指定该模型，不能
  静默假定。
- [ ] 图片模型使用技术路线指定的 `image-2`；若账号不可用，记录实际模型和原因。
- [ ] 使用 Unity 6 LTS、URP 2D Renderer 和空模板场景。
- [ ] 确认 SQLite 和 `runs/pilot-20260812-001/` 可写。
- [ ] 将 Python 包、模型和 Unity 的实际版本写入 `versions.txt`。

## 今日主链清单

按顺序执行下表。每项只实现一次真实调用和一个最小成功结果。

| 状态 | 环节 | 最小可验证场景 | 通过条件与证据 |
| --- | --- | --- | --- |
| [ ] | 调用方 -> FastAPI/Pydantic | POST 统一输入和 `run_id`；再用反例 ID 发送缺少输入的请求 | 正例返回相同 ID，生成 `input.json` 和 `job.accepted` 事件；反例返回 `4xx`，不创建反例目录 |
| [ ] | FastAPI/Workflow -> Responses -> WorldSpec | 用一次真实 Responses Structured Outputs 调用解析统一输入，并直接进入 Pydantic v2 模型 | `world-spec.json` 无人工修补即可通过校验，并包含 `lighthouse`、`pet_wave`、`small_shelter` 和禁止项 `vehicle` |
| [ ] | WorldSpec -> Workflow Runner -> ScenePlan | 用固定模板生成背景层、灯塔锚点、四点活动区、互动点和共建槽位 | `scene-plan.json` 通过契约校验；连续运行两次的结构和坐标相同 |
| [ ] | Workflow Runner -> OpenAIImageProvider | 根据 ScenePlan 调用一次真实 Images API，保存概念图 | `assets/concept.png` 能被 Pillow 打开；`ImageArtifact` 的 URI、MIME、宽高和 SHA-256 与文件实测一致 |
| [ ] | ImageArtifact -> Pillow/OpenCV -> manifest | 将概念图转为标准 PNG 并缩放到 `512 x 288`；用简单矩形 Alpha mask 生成灯塔 RGBA 图，不测试抠图质量 | 两张 PNG 均可重新读取；`asset-manifest.json` 中的尺寸、通道、锚点和 SHA-256 与文件一致 |
| [ ] | ScenePlan + manifest -> Snapshot Builder | Builder 只读取 ScenePlan 和 manifest，生成 Snapshot | `scene-snapshot.json` 通过 `v0.1` JSON Schema；删除一个必填字段后必须失败；文件中没有 Prompt、模型私有字段或绝对路径 |
| [ ] | FastAPI 静态文件 -> Unity Loader | Unity 用 `UnityWebRequest` 下载 Snapshot 和 PNG，C# DTO 解析后创建 Sprite 并按 `sorting_order` 放入 URP 2D 场景 | `unity-screenshot.png` 可见背景和灯塔；Unity 无下载、JSON、纹理或 Sprite 创建错误 |
| [ ] | Snapshot -> Unity 运行时 | 让宠物在活动区内移动、触发内置 `pet_wave`、在槽位放置 `small_shelter` | 三项行为都由 Snapshot 字段驱动；越界移动和未允许的 Prefab 会被拒绝；不修改运行时代码 |
| [ ] | Unity -> Snapshot v2 -> 重载 | 放置小窝后保存 `scene-snapshot-v2.json`，清空场景并仅用 v2 重载 | v2 通过相同 Schema；重载后小窝位置和类型不变；原 Snapshot 仍能单独加载 |
| [ ] | Unity 报告 -> FastAPI/Pydantic -> SQLite | Unity 生成报告并 POST 回内容服务，再按 `run_id` 查询 | `validation-report.json`、SQLite 查询结果和截图使用相同 `run_id` 与 Snapshot 哈希 |
| [ ] | artifact/SQLite -> Workflow 重放 | 重启内容服务，用已有产物重建 Snapshot，不调用模型 | 重建后的业务字段一致，无新 Responses/Images 请求，并写入 `job.replayed` 事件 |

## 可选旁路

只有主链全部通过且本机已有对应环境时，才执行旁路。旁路失败不影响今天的主链
结论。

| 状态 | 环节 | 最小可验证场景 | 通过条件与证据 |
| --- | --- | --- | --- |
| [ ] | ImageArtifact -> 一个 Vision Worker | 只选已安装的 Qwen Layered、Grounded SAM 2、BiRefNet 或 LaMa 之一；输入 `concept.png` 和目标 `lighthouse` | Worker 输出普通 RGBA/mask 和 metadata；内容服务能登记进同一 manifest；停止 Worker 后主链仍可运行 |
| [ ] | Workflow -> ComfyUIProvider | 仅在 ComfyUI 已可用时，向 `/prompt` 提交最小锁版 workflow，并通过 `prompt_id` 取得 PNG | Adapter 返回与 OpenAI Provider 相同结构的 `ImageArtifact`；第 5、6 项无需改代码即可复跑 |

## 失败记录与今日结论

每项失败都按 `输入 -> 调用 -> 实际输出 -> 期望输出` 写入
`validation-report.json`，并记录请求 ID、模型或组件版本和错误阶段。不要保存 API Key。

- [ ] 11 项全通过：主技术栈已经纵向连通，可以进入 M0 完整实现。
- [ ] 部分通过：记录第一个失败边界，下次从该边界继续。
- [ ] 未执行：明确标记“未测试”，不要标记为失败或通过。

Mock 替代真实外部 API、手工复制文件替代接口、Unity 只显示预置图片、失败后手改
JSON，或只凭日志判断产物可用，都不能算“已打通”。
