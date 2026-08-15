# 会话3 验收证据（外部模型 → 内容服务 → Unity，经 HTTP）

本目录保存会话3 的 Unity 端到端验证结果。会话3 验证真实外部模型产物
（Responses Structured Outputs 的 WorldSpec + Images API 概念图）可以进入同一
SceneSnapshot 边界并被 Unity 消费。

## 链路

```
固定场景文本 -> POST /v1/responses (Structured Outputs) -> WorldSpec (Pydantic 校验, 无人工修补)
            -> ScenePlan (固定坐标模板)
            -> POST /v1/images/generations (gpt-image-2) -> b64_json -> 原始 PNG (1774x887)
            -> Pillow/OpenCV 中心裁剪 16:9 + LANCZOS -> 512x288 PNG + ImageArtifact
            -> AssetManifest (实测尺寸/通道/SHA-256) -> SceneSnapshot (v0.1 JSON Schema 校验)
                                                      |
run 目录交付服务(FastAPI) --HTTP--> Unity UnityWebRequest
  GET /snapshot            -> SceneSnapshot JSON
  GET /assets/{id}.png     -> PNG (含真实生成背景)
```

## 验证的运行

- 付费流水线 run 目录：`runs/session3-20260815-023543-bcb8/`（含 `content-ready.json` 标记）
- Responses：原生 `/v1/responses` Structured Outputs（未使用 Chat Completions 适配）
- Images：`gpt-image-2`，HTTP 200，原始 1774 x 887，规范化 512 x 288
- 交付服务：`pilot4mvp/session3/run_server.py --run-dir ...`，不重新调用任何模型
- Unity 场景：复用会话2 的通用 HTTP 消费场景 `Session2Beach`（`HttpSceneSnapshotLoader`
  + `HttpSpriteProvider`，零工程改动，仅新增会话3 测试）

## 结果

- Python 测试：**54 通过**（含 image pipeline、snapshot builder、pipeline fail-closed、交付服务）
- PlayMode 测试：`result="Passed" total=3 passed=3 failed=0 skipped=0`
  （会话1 本地回归 + 会话2 HTTP 回归 + 会话3 真实产物 HTTP 加载）
- 会话3 测试：`Session3HttpLoadingTests.Passed`，日志标记 `PETTRIP_SESSION3_HTTP_OK`
- 背景纹理断言 512 x 288（真实生成图规范化目标画布）
- 截图：`unity-screenshot.png`（真实 gpt-image-2 海边背景 + 灯塔 + pet + 小窝）
- 编排防假通过：端口归属检查、服务 run_id 与付费产物目录一致、XML 定位会话3 用例、
  截图仅认本次新生成（跑前清理旧截图源）

## 通过门槛（规格会话3）

- ✅ 真实 API 产物可被 Pillow 重新打开（管线内双解码校验：Pillow + OpenCV）
- ✅ 哈希与 manifest 一致（`validation-report.json` 的 `manifest_hash_matches: true`；
  ImageArtifact、manifest、文件三者 SHA-256 相等由管线强制）
- ✅ Unity 成功加载（PlayMode 3/3 Passed，背景为真实生成图）
- ✅ 失败可区分（`external_models.py` 错误分类：鉴权/模型不可用/内容策略/超时/解码，
  由 `test_pipeline_fail_closed.py` 与 `test_external_models.py` 负例覆盖）

## 证据文件

- `playmode-results.xml` — NUnit 测试结果（`result="Passed" total="3" passed="3"`）
- `playmode.log` — Unity 测试运行日志（含 `PETTRIP_SESSION3_HTTP_OK`）
- `unity-screenshot.png` — Unity 加载真实产物 Snapshot 后的渲染截图
- `content-service.log` — run 目录交付服务日志

## 适用范围

证明真实外部模型的文本结构化输出与图片产物可以进入同一 SceneSnapshot 边界并被
Unity 经 HTTP 消费。不代表会话4 的报告回传、SQLite 持久化或离线重放已经验证，
也不代表 Chat Completions 适配路径（未启用）已被验收。
