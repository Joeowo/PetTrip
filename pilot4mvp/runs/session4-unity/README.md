# 会话4 验收证据（Unity -> 报告/SQLite -> 离线重放）

本目录保存会话4 的两阶段端到端验证结果。会话4 验证运行结果能够返回内容服务
（验证报告 + SQLite），并在不调用模型的前提下重启重放。以下为 review 修复后
复跑的最终证据（历史 run 与 SQLite 记录全部保留，见下）。

## 链路

```
阶段一（服务 A，统一输入与交互）:
前置: 既有 SQLite 必须已存在且可查询（缺失即打印缺项并停止，不静默初始化）；
      源 run 必须携带既有成功 Snapshot（缺失即停止）
统一输入 POST /runs (run_id + 输入) -> input.json + job.accepted (SQLite + events.jsonl)
    -> 复制源 scene-snapshot.json 为 source-scene-snapshot.json（基线）
    -> 从源 run artifact 复用 world-spec/scene-plan/assets（零模型调用）
    -> 确定性重建 Snapshot v0.2 -> 与源成功快照业务字段强制对比 -> content-ready
Unity: 加载 v0.2 -> 宠物区内移动/越界拒绝 -> 触发 pet_wave
    -> PlacePrefab("small_shelter")（allowed_prefabs 外被拒） -> 上传 v2
服务端: v0.2 JSON Schema + 业务字段一致性校验 -> scene-snapshot-v2.json + placement.json
Unity: 清空重载 v2 -> 小窝位置(430,96)与类型不变 -> 截图 -> POST 报告（显式 run_id）
服务端: run_id + snapshot_sha256 强校验 -> 截图 Pillow 重开 -> SQLite validation_reports

阶段二（服务 B，重启离线重放）:
新进程 + 干净环境（无 OPENAI_*/RESPONSES_*/IMAGES_*）+ --active-run 恢复 run_id
    -> POST /runs/{id}/replay -> 仅从 artifact 重建 v2 -> job.replayed
Unity: 重放快照原样恢复小窝 -> 重放截图
```

## 验证的运行

- 最终 run 目录：`runs/session4-20260815-123037-c247/`（源 run
  `session3-20260815-023543-bcb8`）
- 历史 run 保留：`session4-20260815-115620-0a9e/`（首跑）与
  `session4-20260815-123004-a82d/`（复跑第一遍）均未删除
- Snapshot 哈希：`67d856ef6c9b4dcfbe45c6c8ecde70aa6cf14986c24f72df34264aaf05f9b75`
  （Unity 报告、SQLite `validation_reports`、`evidence-summary.json` 一致）
- SQLite：`runs/content-service.sqlite3`。复跑开场 `existing-verified`
  （`job_events=4` 含前次记录），结束三个 run 各 2 事件 + 1 报告——追加语义
  实证。库文件按仓库 `.gitignore` 约定（transient `*.sqlite3`）不入 git，
  查询结果以 `sqlite-query-snapshot.json` 快照入 git（含开场库状态与全库
  run 清单）；重跑编排保留库并重新生成快照。
- 编排入口：`pilot4mvp/session4/run_unity_session4.py`（防假通过：端口归属、
  run_id 一致、XML 用例定位、截图只认本次新生成、服务日志无模型端点痕迹；
  白名单式清场——只删编排自产输出，README 与 EditMode 证据保留）

## 结果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| SQLite 前置验收 | `existing-verified`，历史保留追加 | `sqlite-query-snapshot.json` 的 `db_state_at_start` |
| 源成功 Snapshot 消费 | 复制 + 业务字段基线对比 | run 目录 `source-scene-snapshot.json`；pytest 篡改 fail-closed 用例 |
| 统一输入正例 | 201，`input.json` + `job.accepted` | run 目录 `input.json`、`events.jsonl` |
| 统一输入负例（缺输入） | `422`，未创建运行目录 | `content-service.log`（编排内实测） |
| Unity 交互（移动/越界/pet_wave/放置/未允许拒绝） | PlayMode 1/1 Passed | `playmode-interaction-results.xml` |
| v2 上传与重载 | 位置与类型不变 | run 目录 `scene-snapshot-v2.json`、`placement.json` |
| 报告回传 + SQLite | 报告显式 `run_id`，四方直接交叉核验 | run 目录 `unity-report.json`（含 `run_id`）、`screenshots/`、SQLite |
| 重启重放 | `business_fields_match: true`，`model_calls: none` | `content-service-replay.log`、SQLite `job.replayed` |
| 重放加载 | PlayMode 1/1 Passed，小窝恢复 | `playmode-replay-results.xml`、`unity-replay-screenshot.png` |
| 回归 | EditMode 14/14；session2 18、session3 54、session4 38 pytest | 各自测试目录 |

## 文件

- `playmode-interaction-results.xml` / `playmode-interaction.log`：阶段一 Unity 结果
- `playmode-replay-results.xml` / `playmode-replay.log`：阶段二 Unity 结果
- `unity-screenshot.png`：v2 重载后（小窝已放置）
- `unity-replay-screenshot.png`：离线重放加载后
- `content-service.log` / `content-service-replay.log`：两阶段服务日志
- `sqlite-query-snapshot.json`：SQLite 直查快照（开场库状态 + 本次 run 事件/报告 + 全库 run 清单）
- `editmode-results.xml` / `editmode.log`：EditMode 14/14（含 v0.1 契约回归）
- `evidence-summary.json`：编排汇总（run_id、哈希、事件序列、开场库状态）
