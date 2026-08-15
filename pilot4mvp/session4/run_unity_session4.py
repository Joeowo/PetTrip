"""会话4 Unity 端到端验证编排（两阶段）。

阶段一（交互与回传）: 启动服务 A -> POST /runs 统一输入（含负例）-> Unity
PlayMode 只跑 Session4InteractionTests（加载 v0.2 -> 移动/越界拒绝 -> pet_wave
-> 放置与未允许拒绝 -> 上传 v2 -> 重载 -> 截图 -> 报告回传）-> SQLite 与
run 目录证据断言 -> 关服务 A。

阶段二（重启离线重放）: 启动服务 B（--active-run 恢复同一 run_id，进程全新、
无任何 OPENAI_* 环境）-> POST replay（仅从 artifact 重建，写入 job.replayed）
-> Unity 只跑 Session4ReplayTests（重放快照恢复小窝位置与类型）-> 断言
job 事件序列与"无模型调用"证据 -> 关服务 B。

防假通过措施（沿用会话2/3 编排设计）:
- 端口归属: 启动前确认 127.0.0.1:8000 空闲；health 后确认子进程存活；
  阶段二 /health 与 /run-id 必须返回本次 run_id。
- 不复用旧结果: 跑前清理 runs/session4-unity/ 与 TestArtifacts/Session4/，
  截图只认本次新生成。
- 测试存在性: 解析 XML 必须定位到 Session4InteractionTests 与
  Session4ReplayTests 且 Passed；-testFilter 只跑会话4 用例（旧会话测试
  断言 0.1 契约，在会话4 服务下不适用）。
- 无模型调用: 服务进程环境不注入任何 OPENAI_/RESPONSES_/IMAGES_ 变量，
  服务代码不 import 外部模型客户端；阶段二后断言事件表 model_calls=none
  且服务日志无外呼端点。

前置: 会话3 成功 run 目录存在（content-ready.json）；Session2Beach 场景已生成。
运行: 在 pilot4mvp/session4/ 下执行
    python run_unity_session4.py --python <venv python> [--source-run-dir ../runs/session3-...]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1] / "unity" / "PetTrip"
UNITY_EXE = r"C:\Program Files\Unity\Hub\Editor\6000.3.21f1\Editor\Unity.exe"
RUNS_DIR = ROOT.parent / "runs"
EVIDENCE = RUNS_DIR / "session4-unity"
DEFAULT_SOURCE_RUN = RUNS_DIR / "session3-20260815-023543-bcb8"
DEFAULT_PY = ROOT.parent / ".venv" / "Scripts" / "python.exe"
DB_PATH = RUNS_DIR / "content-service.sqlite3"
HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"
SESSION_INPUT = "生成一个横向 2D 海边场景，包含一座灯塔；宠物可以在灯塔前挥手；右侧可以放置一个小窝；不要出现车辆。"
SCREENSHOT_DIR = PROJECT / "TestArtifacts" / "Session4"
MODEL_CALL_MARKERS = ("openai", "responses", "images/generations", "/v1/chat")


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait_health(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE_URL}/health", timeout=2).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def start_service(python: str, source_run_dir: Path, run_id: str | None) -> subprocess.Popen:
    """在无任何模型凭证的干净环境中启动会话4 服务。"""
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("OPENAI_", "RESPONSES_", "IMAGES_"))}
    command = [
        python, str(ROOT / "run_server.py"),
        "--source-run-dir", str(source_run_dir),
        "--state-dir", str(RUNS_DIR),
        "--db", str(DB_PATH),
    ]
    if run_id is not None:
        command += ["--active-run", run_id]
    log = EVIDENCE / ("content-service-replay.log" if run_id else "content-service.log")
    return subprocess.Popen(
        command, cwd=str(ROOT), stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT, env=env
    )


def parse_test_summary(xml_path: Path) -> dict:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    return {
        "result": root.get("result"),
        "total": int(root.get("total") or 0),
        "passed": int(root.get("passed") or 0),
        "failed": int(root.get("failed") or 0),
        "skipped": int(root.get("skipped") or 0),
    }


def find_case_result(xml_path: Path, marker: str) -> str | None:
    tree = ET.parse(xml_path)
    for case in tree.iter("test-case"):
        if marker in (case.get("fullname") or ""):
            return case.get("result")
    return None


def run_unity_tests(test_filter: str, xml_path: Path, log_path: Path) -> dict:
    command = [
        UNITY_EXE, "-batchmode",
        "-projectPath", str(PROJECT),
        "-runTests",
        "-testPlatform", "PlayMode",
        "-testFilter", test_filter,
        "-testResults", str(xml_path),
        "-logFile", str(log_path),
    ]
    completed = subprocess.run(command, cwd=str(PROJECT.parent))
    print(f"    Unity 进程退出码={completed.returncode} (仅参考, 判定以 XML 为准)")
    if not xml_path.exists():
        print("    失败: 测试未生成结果 XML, 见 " + log_path.name, file=sys.stderr)
        raise SystemExit(2)
    summary = parse_test_summary(xml_path)
    print(f"    汇总: {summary}")
    if (summary["result"] != "Passed" or summary["failed"] != 0
            or summary["skipped"] != 0 or summary["total"] == 0):
        print(f"    失败: 测试未全部通过 {summary}", file=sys.stderr)
        raise SystemExit(3)
    return summary


def query_sqlite(query: str, params: tuple) -> list[dict]:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params).fetchall()]


def verify_or_init_database() -> dict | None:
    """既有库验证可查询；没有库时借 RunStore 建表初始化。返回 None 表示验证失败。"""
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as connection:
                for table in ("job_events", "validation_reports"):
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return {
                "status": "existing-verified",
                "job_events_rows": query_sqlite("SELECT COUNT(*) AS n FROM job_events", ())[0]["n"],
                "validation_reports_rows": query_sqlite("SELECT COUNT(*) AS n FROM validation_reports", ())[0]["n"],
            }
        except sqlite3.Error:
            return None
    from content_service.run_store import RunStore

    RunStore(RUNS_DIR, DB_PATH)  # 首次初始化建表（幂等 CREATE IF NOT EXISTS）
    return {"status": "initialized", "job_events_rows": 0, "validation_reports_rows": 0}


def require(condition: bool, message: str) -> None:
    if not condition:
        print("    失败: " + message, file=sys.stderr)
        raise SystemExit(4)


def main() -> int:
    parser = argparse.ArgumentParser(description="会话4 两阶段端到端编排")
    parser.add_argument("--python", default=str(DEFAULT_PY), help="启动内容服务使用的 Python 解释器")
    parser.add_argument("--source-run-dir", default=str(DEFAULT_SOURCE_RUN), help="会话3 成功 run 目录")
    args = parser.parse_args()
    source_run_dir = Path(args.source_run_dir)
    if not source_run_dir.is_absolute():
        source_run_dir = (ROOT / source_run_dir).resolve()
    require((source_run_dir / "content-ready.json").is_file(), "源 run 目录不是 content-ready: " + str(source_run_dir))
    require((source_run_dir / "scene-snapshot.json").is_file(),
            "源 run 缺少既有成功 Snapshot (scene-snapshot.json): " + str(source_run_dir))
    require(Path(UNITY_EXE).is_file(), "Unity Editor 不存在: " + UNITY_EXE)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"session4-{stamp}-{secrets.token_hex(2)}"
    run_dir = RUNS_DIR / run_id

    if _port_open(HOST, PORT):
        print(f"    失败: {HOST}:{PORT} 已被占用, 可能有残留服务, 请先释放端口", file=sys.stderr)
        return 5

    # 前置验收 SQLite：存在则必须可查询（历史记录保留，本次只追加）；不存在则首次初始化。
    # 不删除既有数据库，也不删除任何历史 run 目录。
    db_state = verify_or_init_database()
    if db_state is None:
        print("    失败: 既有 SQLite 数据库存在但不可查询", file=sys.stderr)
        return 10
    print(f"    SQLite {db_state['status']}: job_events={db_state['job_events_rows']}"
          f" validation_reports={db_state['validation_reports_rows']} (既有记录保留, 仅追加)")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # 白名单式清理：只删本编排会重新生成的输出；README 与 EditMode 证据保留。
    for name in (
        "playmode-interaction-results.xml", "playmode-interaction.log",
        "playmode-replay-results.xml", "playmode-replay.log",
        "unity-screenshot.png", "unity-replay-screenshot.png",
        "content-service.log", "content-service-replay.log",
        "evidence-summary.json", "sqlite-query-snapshot.json",
    ):
        stale = EVIDENCE / name
        if stale.exists():
            stale.unlink()
    if SCREENSHOT_DIR.exists():
        shutil.rmtree(SCREENSHOT_DIR)

    # ---------- 阶段一: 统一输入 + 交互 + v2 + 报告 ----------

    print(f"[1/6] 启动服务 A 并统一输入 run_id={run_id}...")
    service_a = start_service(args.python, source_run_dir, run_id=None)
    try:
        require(wait_health(), "服务 A 未就绪, 见 content-service.log")
        require(service_a.poll() is None, "服务 A 子进程已退出(端口绑定失败?)")

        accepted = httpx.post(f"{BASE_URL}/runs", json={"run_id": run_id, "input": SESSION_INPUT}, timeout=30)
        require(accepted.status_code == 201, f"统一输入被拒绝: {accepted.status_code} {accepted.text}")
        print(f"    统一输入通过 snapshot={accepted.json()['snapshot']}")

        negative = httpx.post(f"{BASE_URL}/runs", json={"run_id": "session4-negative"}, timeout=10)
        require(400 <= negative.status_code < 500, f"负例应返回 4xx, 实际 {negative.status_code}")
        require(not (RUNS_DIR / "session4-negative").exists(), "负例不得创建运行目录")
        print(f"    负例通过: 缺输入返回 {negative.status_code} 且未创建目录")

        served = httpx.get(f"{BASE_URL}/run-id", timeout=5).json()["run_id"]
        require(served == run_id, f"服务 run_id={served} 与期望 {run_id} 不一致")

        print("[2/6] 运行 Unity PlayMode（交互流）...")
        interaction_xml = EVIDENCE / "playmode-interaction-results.xml"
        run_unity_tests("PetTrip.Tests.Session4InteractionTests", interaction_xml, EVIDENCE / "playmode-interaction.log")
        require(
            find_case_result(interaction_xml, "Session4InteractionTests") == "Passed",
            "未找到通过状态的 Session4InteractionTests",
        )
        interaction_shot = SCREENSHOT_DIR / "unity-screenshot.png"
        require(interaction_shot.is_file(), "本次测试未生成交互流截图")
        shutil.copy2(interaction_shot, EVIDENCE / "unity-screenshot.png")

        print("[3/6] 校验 SQLite 与 run 目录证据...")
        meta = httpx.get(f"{BASE_URL}/snapshot-meta", timeout=5).json()
        require(meta["snapshot"] == "scene-snapshot-v2.json", "活动快照应为 v2")
        detail = httpx.get(f"{BASE_URL}/runs/{run_id}", timeout=5).json()
        events = [event["event"] for event in detail["events"]]
        require(events == ["job.accepted"], f"事件序列应只有 job.accepted, 实际 {events}")
        reports = detail["reports"]
        require(len(reports) == 1, f"应恰有 1 条报告, 实际 {len(reports)}")
        require(reports[0]["snapshot_sha256"] == meta["sha256"], "报告与快照哈希不一致")
        direct = query_sqlite(
            "SELECT run_id, snapshot_sha256 FROM validation_reports WHERE run_id = ?", (run_id,)
        )
        require(len(direct) == 1 and direct[0]["snapshot_sha256"] == meta["sha256"], "SQLite 直查与 API 不一致")
        for name in ("input.json", "scene-snapshot.json", "scene-snapshot-v2.json", "placement.json",
                     "unity-report.json", "events.jsonl", "content-ready.json"):
            require((run_dir / name).is_file(), "run 目录缺证据文件: " + name)
        print(f"    证据齐备 run_dir={run_dir.name} sha256={meta['sha256'][:12]}")
    finally:
        service_a.terminate()
        try:
            service_a.wait(timeout=5)
        except Exception:  # noqa: BLE001
            service_a.kill()

    # ---------- 阶段二: 重启 + 离线重放 ----------

    print("[4/6] 重启服务 B 并离线重放...")
    service_b = start_service(args.python, source_run_dir, run_id=run_id)
    try:
        require(wait_health(), "服务 B 未就绪, 见 content-service-replay.log")
        require(service_b.poll() is None, "服务 B 子进程已退出")
        served = httpx.get(f"{BASE_URL}/run-id", timeout=5).json()["run_id"]
        require(served == run_id, f"重启后 run_id={served} 与期望 {run_id} 不一致")

        replay = httpx.post(f"{BASE_URL}/runs/{run_id}/replay", timeout=30)
        require(replay.status_code == 200, f"重放失败: {replay.status_code} {replay.text}")
        body = replay.json()
        require(body["business_fields_match"] is True, "重放业务字段不一致")
        require(body["snapshot"] == "scene-snapshot-v2.json", "重放目标应为 v2")
        print(f"    重放通过 sha256={body['sha256'][:12]} model_calls=none")

        print("[5/6] 运行 Unity PlayMode（重放加载）...")
        replay_xml = EVIDENCE / "playmode-replay-results.xml"
        run_unity_tests("PetTrip.Tests.Session4ReplayTests", replay_xml, EVIDENCE / "playmode-replay.log")
        require(
            find_case_result(replay_xml, "Session4ReplayTests") == "Passed",
            "未找到通过状态的 Session4ReplayTests",
        )
        replay_shot = SCREENSHOT_DIR / "unity-replay-screenshot.png"
        require(replay_shot.is_file(), "本次测试未生成重放截图")
        shutil.copy2(replay_shot, EVIDENCE / "unity-replay-screenshot.png")

        print("[6/6] 校验重放事件与无模型调用证据...")
        events = query_sqlite(
            "SELECT event, detail FROM job_events WHERE run_id = ? ORDER BY id", (run_id,)
        )
        require([row["event"] for row in events] == ["job.accepted", "job.replayed"],
                f"事件序列应为 accepted+replayed, 实际 {[row['event'] for row in events]}")
        replay_detail = json.loads(events[-1]["detail"])
        require(replay_detail.get("model_calls") == "none", "重放事件必须声明 model_calls=none")

        for log_name in ("content-service.log", "content-service-replay.log"):
            log_text = (EVIDENCE / log_name).read_text(encoding="utf-8", errors="ignore").lower()
            for marker in MODEL_CALL_MARKERS:
                require(marker not in log_text, f"服务日志出现模型调用痕迹 {marker}: {log_name}")

        # SQLite 库文件按仓库约定不入 git（transient），查询结果快照作为 git 内证据
        sqlite_snapshot = {
            "db_path": str(DB_PATH.relative_to(RUNS_DIR)),
            "db_state_at_start": db_state,
            "run_events": query_sqlite(
                "SELECT run_id, event, detail, created_at FROM job_events WHERE run_id = ? ORDER BY id",
                (run_id,),
            ),
            "run_reports": query_sqlite(
                "SELECT run_id, snapshot_sha256, screenshot_filename, screenshot_sha256, created_at"
                " FROM validation_reports WHERE run_id = ? ORDER BY id",
                (run_id,),
            ),
            "all_runs_in_db": query_sqlite(
                "SELECT run_id, COUNT(*) AS events FROM job_events GROUP BY run_id ORDER BY run_id",
                (),
            ),
        }
        (EVIDENCE / "sqlite-query-snapshot.json").write_text(
            json.dumps(sqlite_snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        marker = run_dir / "content-ready.json"
        ready = json.loads(marker.read_text(encoding="utf-8"))
        ready["unity"] = "passed"
        ready["unity_evidence"] = "session4-unity/"
        ready["replayed"] = True
        marker.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        summary = {
            "run_id": run_id,
            "source_run_id": source_run_dir.name,
            "snapshot_sha256": body["sha256"],
            "events": [row["event"] for row in events],
            "db_state_at_start": db_state,
            "evidence_dir": EVIDENCE.name,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        (EVIDENCE / "evidence-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("ALL_CHECKS_PASSED run_id=" + run_id)
        return 0
    finally:
        service_b.terminate()
        try:
            service_b.wait(timeout=5)
        except Exception:  # noqa: BLE001
            service_b.kill()


if __name__ == "__main__":
    raise SystemExit(main())
