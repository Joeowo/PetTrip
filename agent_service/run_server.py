"""会话 1 本地服务启动入口。"""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_settings


def main() -> None:
    """以单进程模式启动服务，避免重复 Worker 和重复模型调用。"""
    settings = load_settings()
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
