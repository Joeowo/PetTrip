"""会话2 Unity PlayMode 端到端验证编排。

启动 Python 内容服务 -> 运行 Unity PlayMode 测试(UnityWebRequest 经 HTTP 加载
SceneSnapshot 与 PNG) -> 关闭服务 -> 整理证据到 pilot4mvp/runs/session2-unity/。

前置: Session2Beach 场景已由 Session2ProjectBootstrap.Create 生成。
运行: 在 pilot4mvp/session2/ 下执行
    ../../.venv/Scripts/python.exe run_unity_session2.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1] / "unity" / "PetTrip"
UNITY_EXE = r"C:\Program Files\Unity\Hub\Editor\6000.3.21f1\Editor\Unity.exe"
EVIDENCE = ROOT.parent / "runs" / "session2-unity"
VENV_PY = ROOT.parent / ".venv" / "Scripts" / "python.exe"
BASE_URL = "http://127.0.0.1:8000"


def wait_health(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    test_results = EVIDENCE / "playmode-results.xml"
    unity_log = EVIDENCE / "unity-playmode.log"
    server_log = EVIDENCE / "content-service.log"

    print("[1/3] 启动内容服务...")
    with server_log.open("w", encoding="utf-8") as server_output:
        server = subprocess.Popen(
            [str(VENV_PY), str(ROOT / "run_server.py")],
            cwd=str(ROOT),
            stdout=server_output,
            stderr=subprocess.STDOUT,
        )
    try:
        if not wait_health():
            print("    内容服务未就绪，见 content-service.log", file=sys.stderr)
            return 1
        run_id = httpx.get(f"{BASE_URL}/run-id", timeout=5).json()["run_id"]
        print(f"    内容服务就绪 run_id={run_id}")

        print("[2/3] 运行 Unity PlayMode 测试...")
        # 注意: PlayMode 测试需要真实 GfxDevice(渲染场景+截图), 不能用 -nographics;
        # -runTests 完成后自动退出, 不加 -quit(与会话1 验证通过的命令一致)。
        cmd = [
            UNITY_EXE, "-batchmode",
            "-projectPath", str(PROJECT),
            "-runTests",
            "-testPlatform", "PlayMode",
            "-testResults", str(test_results),
            "-logFile", str(unity_log),
        ]
        completed = subprocess.run(cmd, cwd=str(PROJECT.parent))
        print(f"    Unity 退出码={completed.returncode}")

        screenshot = PROJECT / "TestArtifacts" / "Session2" / "unity-screenshot.png"
        if screenshot.exists():
            shutil.copy2(screenshot, EVIDENCE / "unity-screenshot.png")
            print("    截图已复制")
        else:
            print("    警告: 未找到测试截图", file=sys.stderr)
        return completed.returncode
    finally:
        print("[3/3] 关闭内容服务...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:  # noqa: BLE001
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
