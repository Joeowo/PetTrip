"""会话2 Unity PlayMode 端到端验证编排。

启动 Python 内容服务 -> 运行 Unity PlayMode 测试 -> 解析结果 XML 判定真通过
(result=Passed 且 failed=0 且 skipped=0 且 total>0) -> 断言截图 -> 关闭服务。

关键防假通过措施:
- 跑前清理旧 playmode-results.xml / playmode.log / 截图, 不复用上次结果;
- 判定以解析 XML 为准, 不依赖 Unity 退出码(此前 -nographics 下退出码 0 但测试被跳过);
- 截图缺失即失败, 不是警告;
- log 文件名为 playmode.log(非 unity-playmode.log), 避开 .gitignore 的 unity-*.log。

前置: Session2Beach 场景已由 Session2ProjectBootstrap.Create 生成。
运行: pilot4mvp/.venv/Scripts/python.exe pilot4mvp/session2/run_unity_session2.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
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


def parse_test_summary(xml_path: Path) -> dict:
    """解析 NUnit <test-run> 根节点的汇总属性。"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    return {
        "result": root.get("result"),
        "total": int(root.get("total") or 0),
        "passed": int(root.get("passed") or 0),
        "failed": int(root.get("failed") or 0),
        "skipped": int(root.get("skipped") or 0),
    }


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    test_results = EVIDENCE / "playmode-results.xml"
    unity_log = EVIDENCE / "playmode.log"
    screenshot_dst = EVIDENCE / "unity-screenshot.png"

    # 清理旧证据, 防止复用上次结果假通过
    for stale in (test_results, unity_log, screenshot_dst):
        if stale.exists():
            stale.unlink()

    print("[1/4] 启动内容服务...")
    server_log = EVIDENCE / "content-service.log"
    with server_log.open("w", encoding="utf-8") as server_output:
        server = subprocess.Popen(
            [str(VENV_PY), str(ROOT / "run_server.py")],
            cwd=str(ROOT),
            stdout=server_output,
            stderr=subprocess.STDOUT,
        )
    try:
        if not wait_health():
            print("    内容服务未就绪, 见 content-service.log", file=sys.stderr)
            return 1
        run_id = httpx.get(f"{BASE_URL}/run-id", timeout=5).json()["run_id"]
        print(f"    服务就绪 run_id={run_id}")

        print("[2/4] 运行 Unity PlayMode 测试...")
        # 不加 -nographics(PlayMode 需真实 GfxDevice) 与 -quit(与 -runTests 重复且可能提前退出)
        cmd = [
            UNITY_EXE, "-batchmode",
            "-projectPath", str(PROJECT),
            "-runTests",
            "-testPlatform", "PlayMode",
            "-testResults", str(test_results),
            "-logFile", str(unity_log),
        ]
        completed = subprocess.run(cmd, cwd=str(PROJECT.parent))
        print(f"    Unity 进程退出码={completed.returncode} (仅参考, 判定以 XML 为准)")

        print("[3/4] 解析结果 XML 判定真通过...")
        if not test_results.exists():
            print("    失败: 测试未生成结果 XML(测试可能未启动/被跳过), 见 playmode.log", file=sys.stderr)
            return 2
        summary = parse_test_summary(test_results)
        print(f"    汇总: {summary}")
        if (summary["result"] != "Passed" or summary["failed"] != 0
                or summary["skipped"] != 0 or summary["total"] == 0):
            print(f"    失败: 测试未全部通过 {summary}", file=sys.stderr)
            return 3

        print("[4/4] 校验截图证据...")
        screenshot_src = PROJECT / "TestArtifacts" / "Session2" / "unity-screenshot.png"
        if not screenshot_src.exists():
            print("    失败: 测试未生成截图", file=sys.stderr)
            return 4
        shutil.copy2(screenshot_src, screenshot_dst)
        print(f"    通过: 截图已复制到 {screenshot_dst.name}")
        print("ALL_CHECKS_PASSED")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:  # noqa: BLE001
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
