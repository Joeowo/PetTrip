# 会话 1 验收证据

本目录保存 `pilot-20260812-001` 的真实会话 1 验收结果。该会话只验证人工
`SceneSnapshot` 被 Unity 6 URP 2D 运行时消费；不包含 Python、HTTP、OpenAI、
SQLite 或任何模型调用。

## 输入与版本

- `scene_id`：`session1_beach`
- `schema_version`：`0.1`
- 画布：`512 x 288`，每单位 16 像素
- Unity：`6000.3.21f1`
- Universal Render Pipeline：`17.3.0`
- Unity Test Framework：`1.6.0`

## 结果

- EditMode：4 个测试通过，覆盖合法 fixture、错误版本、车辆资产和越界坐标。
- PlayMode：1 个测试通过，确认 Snapshot 构建背景、灯塔、宠物、活动区、
  `pet_wave` 和右侧 `small_shelter`。
- 截图：`unity-screenshot.png`，尺寸为 512 x 288。
- 加载日志：`load-success.log`，含成功加载、互动和截图写入标记。

这证明 Unity 的人工 Snapshot 消费边界成立，不代表会话 2 到会话 4 的服务或模型链路
已经验证。
