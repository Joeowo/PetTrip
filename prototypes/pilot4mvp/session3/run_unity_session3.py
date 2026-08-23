"""会话3 Unity PlayMode 端到端验证编排。

从付费流水线 run 目录提供 Snapshot -> 运行 Unity PlayMode 测试 -> 解析结果 XML
判定真通过 -> 断言本次新生成的截图 -> 关闭服务。

防假通过措施（沿用会话2 编排设计）:
- 端口归属: 启动前确认 127.0.0.1:8000 空闲(被占即报错, 不测残留旧服务); health 通过后
  确认子进程仍存活; /run-id 必须等于 --run-dir 指定的 run_id, 证明服务来自本次流水线产物。
- 不复用旧结果: 跑前清理编排证据目录, 同时清理 Unity 工程内 TestArtifacts/Session3/
  旧截图, 确保截图来自本次测试。
- 测试存在性: 解析 XML 不只看 total>0, 必须定位到 Session3HttpLoadingTests 且 Passed,
  避免"只跑了会话1/2 + 复用旧会话3 截图"的假通过。
- 判定以 XML 为准, 不依赖 Unity 退出码; 截图缺失即失败(非警告)。
- log 文件名为 playmode.log(非 unity-playmode.log), 避开 .gitignore 的 unity-*.log。

前置: 付费流水线已成功(run 目录含 content-ready.json); Session2Beach 场景已由
Session2ProjectBootstrap.Create 生成(会话3 复用该通用 HTTP 消费场景)。
运行: pilot4mvp/session3/ 下执行
    python run_unity_session3.py --run-dir ../runs/session3-<stamp>-<hex> --python <venv python>
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1] / "unity" / "PetTrip"
UNITY_EXE = r"C:\Program Files\Unity\Hub\Editor\6000.3.21f1\Editor\Unity.exe"
EVIDENCE = ROOT.parent / "runs" / "session3-unity"
DEFAULT_PY = ROOT.parent / ".venv" / "Scripts" / "python.exe"
HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"
SESSION3_SCREENSHOT_SRC = PROJECT / "TestArtifacts" / "Session3" / "unity-screenshot.png"
SESSION3_TEST_MARKER = "Session3HttpLoadingTests"


def _port_open(host: str, port: int) -> bool:
    """探测 (host, port) 是否已有监听者。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


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


def find_session3_result(xml_path: Path) -> str | None:
    """定位会话3 测试用例的 result; 未找到返回 None。"""
    tree = ET.parse(xml_path)
    for case in tree.iter("test-case"):
        if SESSION3_TEST_MARKER in (case.get("fullname") or ""):
            return case.get("result")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="会话3 Unity 端到端编排")
    parser.add_argument("--run-dir", required=True, help="付费流水线 run 目录（含 content-ready.json）")
    parser.add_argument("--python", default=str(DEFAULT_PY), help="启动交付服务使用的 Python 解释器")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = (ROOT / run_dir).resolve()
    if not (run_dir / "content-ready.json").is_file():
        print(f"失败: run 目录不是 content-ready: {run_dir}", file=sys.stderr)
        return 8
    expected_run_id = run_dir.name

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    test_results = EVIDENCE / "playmode-results.xml"
    unity_log = EVIDENCE / "playmode.log"
    screenshot_dst = EVIDENCE / "unity-screenshot.png"

    # 端口检查必须在任何证据清理之前: 端口被占时直接退出, 不破坏现有验收证据
    if _port_open(HOST, PORT):
        print(
            f"    失败: {HOST}:{PORT} 已被占用, 可能有残留内容服务, 请先释放端口",
            file=sys.stderr,
        )
        return 5

    # 端口可用、确认开始新运行后, 才清理旧证据(编排目录 + Unity 工程内截图源)
    for stale in (test_results, unity_log, screenshot_dst, SESSION3_SCREENSHOT_SRC):
        if stale.exists():
            stale.unlink()

    print(f"[1/4] 启动会话3 交付服务 run_id={expected_run_id}...")
    server_log = EVIDENCE / "content-service.log"
    with server_log.open("w", encoding="utf-8") as server_output:
        server = subprocess.Popen(
            [str(args.python), str(ROOT / "run_server.py"), "--run-dir", str(run_dir)],
            cwd=str(ROOT),
            stdout=server_output,
            stderr=subprocess.STDOUT,
        )
    try:
        if not wait_health():
            print("    失败: 交付服务未就绪, 见 content-service.log", file=sys.stderr)
            return 1
        # 健康响应必须来自本次子进程; run_id 必须等于付费流水线产物目录名
        if server.poll() is not None:
            print(
                "    失败: 交付服务子进程已退出(端口绑定失败?), 见 content-service.log",
                file=sys.stderr,
            )
            return 6
        served_run_id = httpx.get(f"{BASE_URL}/run-id", timeout=5).json()["run_id"]
        if served_run_id != expected_run_id:
            print(
                f"    失败: 服务 run_id={served_run_id} 与期望 {expected_run_id} 不一致",
                file=sys.stderr,
            )
            return 9
        print(f"    服务就绪 run_id={served_run_id}")

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
        session3_result = find_session3_result(test_results)
        print(f"    汇总: {summary}")
        print(f"    会话3 测试结果: {session3_result}")
        if (summary["result"] != "Passed" or summary["failed"] != 0
                or summary["skipped"] != 0 or summary["total"] == 0):
            print(f"    失败: 测试未全部通过 {summary}", file=sys.stderr)
            return 3
        if session3_result != "Passed":
            print(
                f"    失败: 未找到通过状态的会话3 测试 session3={session3_result}",
                file=sys.stderr,
            )
            return 7

        print("[4/4] 校验截图证据(本次新生成)...")
        if not SESSION3_SCREENSHOT_SRC.exists():
            print("    失败: 本次测试未生成截图", file=sys.stderr)
            return 4
        shutil.copy2(SESSION3_SCREENSHOT_SRC, screenshot_dst)
        print(f"    通过: 截图已复制到 {screenshot_dst.name}")

        # 把 Unity 结论写回 run 目录, 收口 validation-report 的 unity 字段
        report_path = run_dir / "validation-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["unity"] = "passed"
        report["unity_evidence"] = "session3-unity/"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


if __name__ == "__main__":
    raise SystemExit(main())
