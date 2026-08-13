# 会话2 验收证据（Python 内容服务 → Unity，经 HTTP）

本目录保存会话2 的端到端验证结果。会话2 验证 Python 内容服务通过 HTTP 向 Unity
交付 SceneSnapshot 和 PNG；Unity 只消费 HTTP 取得的文件，不读取本地 Snapshot。

## 链路

```
WorldSpec -> ScenePlan -> AssetManifest -> Snapshot Builder -> SceneSnapshot
                       (Pillow 尺寸 + OpenCV 解码)  (校验 asset 一致性)     |
                                                                        HTTP
Python 内容服务 --HTTP--> Unity UnityWebRequest
  GET /snapshot            -> SceneSnapshot JSON (JsonUtility 解析 + Validator 校验)
  GET /assets/{id}.png     -> PNG (UnityWebRequestTexture 下载, 运行时创建 Sprite)
```

- 内容服务：`pilot4mvp/session2/content_service`（FastAPI，固定模板生成 Snapshot）
- Unity 加载：`HttpSceneSnapshotLoader` + `HttpSpriteProvider`
- 内容边界：SceneSnapshot v0.1（经 `contracts/scene-snapshot/v0.1.schema.json` 校验）
- **AssetManifest 参与构建**：Snapshot Builder 校验 ScenePlan 引用的每个 asset_id /
  build_slot prefab 必须在 manifest 声明，manifest 缺资产或冲突时拒绝发布 Snapshot。
- **资产解码**：Pillow 读取尺寸 + OpenCV 解码完整性校验（证明 OpenCV 参与会话2 链路）。

## 输入与版本

- 内容服务 run_id：`session2-20260813-045919-b542`
- 完整版本清单见 `versions.txt`（Python 解释器 + pip freeze + Unity/URP/Test Framework）
- SceneSnapshot：schema `0.1`，scene `session1_beach`，画布 `512 x 288`

## 结果

- Python 测试：**18 通过**（正例 + AssetManifest 负例 + Schema 负例 + HTTP API）
- 真实 HTTP 自验证：4 PNG 返回 200 可解码；未知资产**强制断言 404**
- PlayMode 测试：`result="Passed" total=2 passed=2 failed=0 skipped=0`
  （会话1 本地加载回归 + 会话2 HTTP 加载）
- 会话2 HTTP 加载：`PETTRIP_HTTP_SNAPSHOT_LOAD_OK layers=3 assets=4`
  （背景 / 灯塔 / pet / small_shelter 共 4 sprite 全部经 HTTP 下载并运行时创建）
- 截图：`unity-screenshot.png`（512 x 288）

## 通过门槛（规格会话2）

- ✅ Unity 只通过 HTTP 取得的文件成功加载（asset 来自 `HttpSpriteProvider`，非预绑定）
- ✅ Snapshot 不含绝对路径或生成模型字段（Python 负例测试 + Schema `additionalProperties:false`
  拒绝额外字段 + Unity 加载校验）
- ✅ AssetManifest → Snapshot 环节被代码强制（manifest 缺资产时 `build_snapshot` 抛错）
- ✅ 不破坏会话1（会话1 本地加载 PlayMode 测试回归通过）

## 证据文件

- `playmode-results.xml` — NUnit 测试结果（`result="Passed" total="2" passed="2"`）
- `playmode.log` — Unity 测试运行日志（文件名不带 `unity-` 前缀，避开 `.gitignore` 的 `unity-*.log`）
- `unity-screenshot.png` — Unity 加载 HTTP Snapshot 后的渲染截图（512 x 288）
- `content-service.log` — FastAPI 内容服务日志
- `versions.txt` — Python / Unity 完整版本清单

## 编排防假通过

`run_unity_session2.py`：跑前清理旧证据 → 解析结果 XML 判真通过（**不依赖 Unity 退出码**，
`result=Passed & failed=0 & skipped=0 & total>0`）→ 断言截图存在 → 失败非 0 退出。避免复用旧
结果或"退出码 0 但测试被跳过"的假通过。

## 适用范围

证明 Python 内容服务与 Unity 可通过 HTTP 交换 SceneSnapshot 与 PNG 素材，且 AssetManifest
契约被强制。不代表会话3 的真实 OpenAI 链路，或会话4 的 SQLite 报告/重放已经验证。
