# 目的地生成数据模型与持久化（T1）

本模块实现了 issue #13 定义的目的地生成数据模型与持久化基座。

## 概述

扩展现有 pilot4mvp2 存储基座，新增 11 张表支持目的地生成流程：

### 新增表结构

**澄清阶段**（2 张表）：
- `clarification_inputs` - 玩家输入记录
- `clarification_state` - 澄清状态管理

**目的地设计**（3 张表）：
- `destinations` - 目的地主表
- `destination_requirements` - 目的地要求集（冻结后不可变）
- `destination_requirement_items` - 要求明细项

**场景规格**（2 张表）：
- `destination_specs` - 目的地规格（锁定后不可变）
- `scene_plans` - 场景计划（每个目的地恰好 2 个，order=0/1）

**制品管理**（3 张表）：
- `shared_environment_artifacts` - 共享环境制品（不可变）
- `scene_artifacts` - 场景制品（不可变）
- `interaction_zones` - 交互区域定义

**操作记录**（2 张表）：
- `prompt_snapshots` - Prompt 快照
- `operation_attempts` - 操作尝试记录

## 数据约束

### 不可变对象
以下对象在创建后不可修改（通过应用层只读断言保护）：
- `destination_requirements`（frozen_at 后）
- `destination_specs`（locked_at 后）
- `shared_environment_artifacts`
- `scene_artifacts`

### 唯一性约束
- `(destination_id, order_index)` → ScenePlan（只允许 0/1）
- `(session_id, input_id)` → ClarificationInput
- `(destination_id, spec_version)` → DestinationSpec

### 外键级联
所有新表都通过外键关联到现有 pilot4mvp2 基座：
- `api_clients` - API 客户端
- `sessions` - 会话
- `runs` - 运行记录
- `files` - 文件元数据

## 使用示例

```python
from pathlib import Path
from pilot4mvp2.agent_service.destination_storage import DestinationRepository
from pilot4mvp2.agent_service.storage import Storage

# 初始化（与现有 Storage 共用数据库）
db_path = Path("data/agent.db")
storage = Storage(db_path)
repo = DestinationRepository(db_path)
repo.open()

# 创建会话和目的地
client_id = storage.upsert_api_client("key_hash", "unity_client")
session = storage.create_session(client_id)
destination = repo.create_destination(
    session_id=session["id"],
    api_client_id=client_id,
)

# 管理澄清状态
state = repo.upsert_clarification_state(
    session_id=session["id"],
    accepted_wish_count=1,
    destination_id=destination["id"],
)

# 记录玩家输入
run = storage.create_run(...)
input_record = repo.create_clarification_input(
    session_id=session["id"],
    run_id=run["id"],
    raw_text="我想去海边",
    classification="accepted_wish_input",
    normalized_text="去海边",
)

# 使用事务进行批量操作
with repo.transaction() as conn:
    conn.execute("INSERT INTO destinations ...")
    conn.execute("INSERT INTO destination_specs ...")
    # 异常会自动回滚

# 关闭
repo.close()
storage.close()
```

## 启动迁移

Repository 在 `open()` 时自动执行建表迁移：
- 在事务中执行，确保原子性
- 使用 `CREATE TABLE IF NOT EXISTS`，不破坏现有数据
- 所有外键约束、CHECK 约束、唯一索引自动创建

## 测试覆盖

6 个必过测试（issue #13 要求）：
1. ✅ 启动时建表成功，不破坏现有数据
2. ✅ 所有外键约束生效
3. ✅ UUID 主键正常生成
4. ✅ 基础插入与查询可执行
5. ✅ 事务回滚时数据不提交
6. ✅ 唯一约束冲突时抛出可识别错误

额外测试：
7. ✅ CHECK 约束验证（phase 枚举、order_index 范围等）

运行测试：
```bash
cd pilot4mvp2
.venv/Scripts/python -m pytest agent_service/test_destination_storage.py -v
```

## 范围限制（明确不包含）

根据 issue #13，以下内容**不在此阶段**实现：
- ❌ 复杂业务查询逻辑（留给后续任务）
- ❌ Manifest 投影（留给 T8）
- ❌ LangGraph checkpoint 集成
- ❌ 完整恢复逻辑（留给 T4）

## 相关文档

- **父级规范**：[issue #10](https://github.com/Joeowo/PetTrip/issues/10) - 统一目的地数据模型与 Unity 交付契约
- **本任务**：[issue #13](https://github.com/Joeowo/PetTrip/issues/13) - T1: 数据模型与持久化基座
- **领域语言**：`CONTEXT.md`
- **契约文档**：`docs/contracts/README.md`
- **现有存储**：`pilot4mvp2/agent_service/storage.py`
