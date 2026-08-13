# 会话2 验收证据（Python 内容服务 → Unity，经 HTTP）

本目录保存会话2 的端到端验证结果。会话2 验证 Python 内容服务通过 HTTP 向 Unity
交付 SceneSnapshot 和 PNG；Unity 只消费 HTTP 取得的文件，不读取本地 Snapshot。

## 链路

```
Python 内容服务 --HTTP--> Unity UnityWebRequest
  GET /snapshot            -> SceneSnapshot JSON (经 JsonUtility 解析 + Validator 校验)
  GET /assets/{id}.png     -> PNG (UnityWebRequestTexture 下载, 运行时创建 Sprite)
```

- 内容服务：`pilot4mvp/session2/content_service`（FastAPI，固定模板生成 Snapshot）
- Unity 加载：`HttpSceneSnapshotLoader` + `HttpSpriteProvider`
- 内容边界：SceneSnapshot v0.1（经 `contracts/scene-snapshot/v0.1.schema.json` 校验）

## 输入与版本

- 内容服务 run_id：`session2-20260813-041342-a61b`
- SceneSnapshot：schema `0.1`，scene `session1_beach`，画布 `512 x 288`
- Unity：`6000.3.21f1`，URP `17.3.0`，Test Framework `1.6.0`
- Python：`3.12`，FastAPI `0.141.1`，Pydantic `2.13.4`，Pillow `12.3.0`

## 结果

- PlayMode 测试：**2 通过，0 失败**（会话1 回归 + 会话2 HTTP 加载）
- 会话2 HTTP 加载：`PETTRIP_HTTP_SNAPSHOT_LOAD_OK layers=3 assets=4`
  （背景 / 灯塔 / pet / small_shelter 共 4 张 sprite 全部经 HTTP 下载并运行时创建）
- 截图：`unity-screenshot.png`（512 x 288）

## 通过门槛（规格会话2）

- ✅ Unity 只通过 HTTP 取得的文件成功加载（asset 来自 `HttpSpriteProvider`，非预绑定）
- ✅ Snapshot 不含绝对路径或生成模型字段（Python 侧 `test_snapshot_has_no_absolute_path_or_model_fields` + Unity 加载校验）
- ✅ 不破坏会话1（会话1 本地加载 PlayMode 测试回归通过）

## 证据文件

- `playmode-results.xml` — NUnit 测试结果（`result="Passed" total="2" passed="2"`）
- `unity-playmode.log` — Unity 测试运行日志
- `unity-screenshot.png` — Unity 加载 HTTP Snapshot 后的渲染截图
- `content-service.log` — FastAPI 内容服务日志

## 适用范围

证明 Python 内容服务与 Unity 可通过 HTTP 交换 SceneSnapshot 与 PNG 素材。不代表
会话3 的真实 OpenAI 链路，或会话4 的 SQLite 报告/重放已经验证。
