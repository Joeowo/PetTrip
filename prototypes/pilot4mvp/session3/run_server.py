"""会话3 快照交付服务启动入口。

从 --run-dir 指定的付费流水线产物目录读取 Snapshot 与 PNG，在 127.0.0.1:8000
用稳定 URI 提供；不重新调用任何模型。

运行: 在 pilot4mvp/session3/ 下执行
    python run_server.py --run-dir ../runs/session3-<stamp>-<hex>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from content_service.server import create_app

SESSION3_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提供会话3 真实产物 Snapshot 的交付服务")
    parser.add_argument("--run-dir", required=True, help="付费流水线 run 目录（含 content-ready.json）")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = (SESSION3_ROOT / run_dir).resolve()
    app = create_app(run_dir)
    print(f"SESSION3_SERVER_READY run_id={app.state.run_id}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
