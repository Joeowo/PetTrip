"""启动会话 7 Quick Tunnel，不把地址或 origin 写入日志。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from pilot4mvp2.scripts.run_session7_server import (
    _default_local_root,
    protect_private_directory,
)

URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def _write_private_url(url_file: Path, url: str) -> None:
    url_file.parent.mkdir(parents=True, exist_ok=True)
    protect_private_directory(url_file.parent)
    url_file.write_text(f"{url}\n", encoding="utf-8", newline="\n")


def main() -> int:
    cloudflared = shutil.which("cloudflared")
    if cloudflared is None and os.name == "nt":
        installed = Path("C:/Program Files (x86)/cloudflared/cloudflared.exe")
        cloudflared = str(installed) if installed.is_file() else None
    if cloudflared is None:
        print("会话 7 隧道未启动：找不到 cloudflared。", file=sys.stderr)
        return 2

    origin_port = os.environ.get("PETTRIP_SESSION7_PORT", "8001").strip()
    if not origin_port.isdecimal() or not 1 <= int(origin_port) <= 65535:
        print("会话 7 隧道未启动：端口配置不合法。", file=sys.stderr)
        return 2
    local_root = _default_local_root()
    local_root.mkdir(parents=True, exist_ok=True)
    protect_private_directory(local_root)
    url_file = local_root / "public-base-url.local"
    if url_file.exists():
        print("会话 7 隧道未启动：私密入口文件已存在。", file=sys.stderr)
        return 2

    process = subprocess.Popen(
        [
            cloudflared,
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{origin_port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    is_ready = threading.Event()
    stdout_finished = threading.Event()

    def consume_output() -> None:
        if process.stdout is None:
            stdout_finished.set()
            return
        for line in process.stdout:
            if is_ready.is_set():
                continue
            match = URL_PATTERN.search(line)
            if match:
                _write_private_url(url_file, match.group(0))
                is_ready.set()
        stdout_finished.set()

    reader = threading.Thread(target=consume_output, name="cloudflared-output", daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not is_ready.is_set():
            if process.poll() is not None or stdout_finished.is_set():
                break
            time.sleep(0.1)
        if not is_ready.is_set():
            print(
                "会话 7 隧道未在时限内提供公网入口。",
                file=sys.stderr,
            )
            return 1
        print(
            "PetTrip 会话 7 公网 HTTPS 入口已就绪；"
            "完整地址仅保存在仓库外私密文件中。"
        )
        return_code = process.wait()
        reader.join(timeout=1)
        if return_code != 0:
            print(
                "会话 7 隧道已退出；原始输出未持久化或回显。",
                file=sys.stderr,
            )
            return 1
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)
        url_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
