# PetTrip Agent Service

PetTrip 多模态 Agent 服务的生产代码。

## 目录结构

```
agent_service/
├── *.py                    # 核心模块（13 个）
│   ├── app.py             # Flask 应用主入口
│   ├── chat_provider.py   # 对话服务
│   ├── image_provider.py  # 图片生成服务
│   ├── worker.py          # 后台任务处理
│   ├── storage.py         # 数据存储层
│   ├── destination_storage.py  # 目的地数据模型（T1）
│   ├── file_storage.py    # 文件存储
│   ├── config.py          # 配置管理
│   ├── auth.py            # 认证
│   ├── schemas.py         # 数据模式
│   ├── ids.py             # ID 生成
│   ├── errors.py          # 错误处理
│   ├── run_server.py      # 服务器启动
│   └── structured_output.py  # 结构化输出
├── tests/                 # 测试套件（19 个测试文件）
├── scripts/               # 验证和工具脚本
├── api_test_client/       # API 测试客户端
├── requirements.txt       # Python 依赖
└── .env.example          # 环境变量模板
```

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
