"""PetTrip 多模态 Chatbot Agent Service（自研薄实现）。

本包提供 Pilot 阶段的 Agent 服务：Bearer 鉴权、SQLite 持久化、异步 Run 与后台
Worker、Chat/Vision 与图片生成 Provider 边界。包内不创建顶层 app，避免导入副作用；
正式运行由 ``run_server.py`` 以工厂 ``create_app`` 启动，测试用 ``create_app(...)``
注入伪 Provider 与临时数据库。
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
