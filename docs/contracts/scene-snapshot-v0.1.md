# SceneSnapshot v0.1

本文档冻结会话 1 的 Unity 消费契约。Unity 只消费此版本化 JSON，不读取 Prompt、
模型字段、临时路径或生成服务私有数据。

## 固定场景

会话 1 使用 512 x 288 横向海边场景，包含背景、灯塔、宠物、`pet_wave` 互动点和
右侧 `small_shelter` 共建槽位。像素坐标以画布左下角为原点，Unity 按
`pixels_per_unit` 换算为世界坐标。

机器可读 Schema 位于 `contracts/scene-snapshot/v0.1.schema.json`，运行时 fixture
位于 Unity 工程的 `Assets/StreamingAssets/PetTrip/scene-session-1.json`。

## 拒绝规则

加载器在创建任何场景对象前拒绝错误版本、错误画布、缺少必需对象、重复 ID、越界
坐标、未知资产、非允许 Prefab 和车辆字段。会话 1 不实现版本兼容或硬编码回退。
