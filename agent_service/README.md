# PetTrip Agent Service

PetTrip 多模态 Agent 服务的生产代码。

## 目录结构

```
agent_service/
├── api/                   # HTTP 协议适配层
│   ├── app.py            # FastAPI 应用主入口
│   ├── auth.py           # Bearer 认证中间件
│   └── schemas.py        # 请求/响应数据模型
├── domain/                # 业务逻辑核心
│   ├── runs.py           # Run 状态机和执行流程
│   └── worker.py         # Run 异步执行器
├── adapters/              # 外部服务适配器
│   ├── llm.py            # LLM Provider 统一接口
│   └── image.py          # 图片生成 Provider 统一接口
├── storage/               # 数据持久化层
│   ├── __init__.py       # Storage 向后兼容层
│   ├── database.py       # SQLite CRUD 操作
│   ├── files.py          # 本地文件存储
│   └── models.py         # 数据模型和异常定义
├── shared/                # 跨层共享设施
│   ├── config.py         # 配置管理
│   ├── errors.py         # 错误定义和处理
│   ├── ids.py            # ID 生成工具
│   └── structured_output.py  # 结构化输出注册表
├── tests/                 # 测试套件（19 个测试文件）
├── scripts/               # 验证和工具脚本
├── api_test_client/       # API 测试客户端
├── run_server.py          # 服务器启动入口
├── requirements.txt       # Python 依赖
└── .env.example          # 环境变量模板
```

### 架构原则

本服务采用**轻量分层架构**，基于以下设计原则：

- **深度模块**（Deep Modules）：小接口 + 大实现 = 高杠杆率
- **清晰的依赖规则**：`api/ → domain/ → adapters/storage/ → shared/`
- **统一适配器接口**：Provider 通过 Protocol 定义契约，易于测试和扩展
- **渐进式披露**：现在建立清晰边界，未来按需深化

详见：`docs/adr/0002-layered-architecture-upgrade.md`

## 快速开始

### 1. 安装依赖

```bash
cd agent_service
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env.local
# 编辑 .env.local 填写实际配置
```

### 3. 运行服务

```bash
python -m agent_service.run_server
```

### 4. 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行特定 session 的测试
pytest tests/test_session1_*.py -v
```

## 文档

完整文档位于 `docs/agent_service/`:

- `pettrip-agent-api.md` - Agent API 文档
- `flowise-unity-pilot-spec.md` - Pilot 规格
- `session7-cross-network-acceptance.md` - 跨网络验收

## 历史

这个服务从 `pilot4mvp2/` 演化而来。原型代码已归档到 `prototypes/pilot4mvp2/`。

重组决策详见: `docs/adr/0001-codebase-restructure-production-vs-prototypes.md`

## Issue #1 工作流

所有 Issue #1 (静态目的地闭环) 的后续工作在此目录进行：

1. 从 main 创建 feature 分支
2. 使用 worktree 隔离开发
3. 在 `agent_service/` 中添加代码
4. 通过 PR 合并到 main

## 相关链接

- Issue #1: https://github.com/Joeowo/PetTrip/issues/1
- Issue #26: https://github.com/Joeowo/PetTrip/issues/26 (本次重组)
