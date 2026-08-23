"""会话4 内容服务启动入口。

从 --source-run-dir 指定的会话3 付费流水线产物目录复用上游 artifact；统一输入
POST /runs 后在 --state-dir 下物化会话4 run 目录，SQLite 落在 --db。重启后用
--run-dir 恢复活动 run（离线重放验证）。

运行: 在 pilot4mvp/session4/ 下执行
    python run_server.py --source-run-dir ../runs/session3-<stamp>-<hex> --state-dir ../runs --db ../runs/content-service.sqlite3
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from content_service.server import create_app

SESSION4_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="会话4 统一输入/报告/重放内容服务")
    parser.add_argument("--source-run-dir", required=True, help="会话3 付费流水线 run 目录")
    parser.add_argument("--state-dir", required=True, help="会话4 run 目录所在状态目录")
    parser.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    parser.add_argument("--active-run", default=None, help="重启后恢复的活动 run_id（可选）")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    def resolve(path: str) -> Path:
        value = Path(path)
        return value if value.is_absolute() else (SESSION4_ROOT / value).resolve()

    app = create_app(
        source_run_dir=resolve(args.source_run_dir),
        state_dir=resolve(args.state_dir),
        db_path=resolve(args.db),
        run_id=args.active_run,
    )
    print(f"SESSION4_SERVER_READY run_id={app.state.run_id}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
