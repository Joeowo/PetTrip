"""会话2 内容服务启动入口。

以 persist=True 启动：生成固定 SceneSnapshot、通过 contracts 校验、把该次运行的
WorldSpec/ScenePlan/AssetManifest/SceneSnapshot 落盘到 pilot4mvp/runs/<run_id>/，
并在 127.0.0.1:8000 用稳定 URI 提供 Snapshot 与 PNG。

运行: 在 pilot4mvp/session2/ 下执行
    ../../.venv/Scripts/python.exe run_server.py
"""

from __future__ import annotations

import uvicorn

from content_service.app import create_app

app = create_app(persist=True)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
