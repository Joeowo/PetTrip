"""自我复验(修复 P1): 离线脚本与端口占用路径都不删除正式验收证据。

验证两点:
1. verify_orchestration.py 不执行带副作用的 main(), 跑完证据仍齐全;
2. 端口被占时 run_unity_session2.main() 在清理证据之前 return 5, 证据不被删除。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
EVIDENCE = ROOT.parent / "runs" / "session2-unity"
KEY_FILES = ["playmode-results.xml", "playmode.log", "unity-screenshot.png"]


def evidence_intact() -> bool:
    return all((EVIDENCE / f).exists() for f in KEY_FILES)


def main() -> int:
    assert evidence_intact(), "复验前提: 正式验收证据应已存在"
    print("[1] before: 证据齐全")

    # 1. verify_orchestration 不执行 main(), 不应有文件副作用
    rc = subprocess.run([PY, str(ROOT / "verify_orchestration.py")], capture_output=True).returncode
    assert rc == 0, f"verify_orchestration 应成功, rc={rc}"
    assert evidence_intact(), "verify_orchestration 后正式证据缺失"
    print("[2] verify_orchestration 通过, 正式证据保留")

    # 2. 端口被占时 main 在清理证据前 return 5, 证据不被删
    occupant = subprocess.Popen(
        [PY, "-m", "http.server", "8000"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)
    try:
        rc = subprocess.run([PY, str(ROOT / "run_unity_session2.py")], capture_output=True).returncode
        assert rc == 5, f"端口占用应 return 5, 得 {rc}"
        assert evidence_intact(), "main 端口占用后正式证据缺失"
        print(f"[3] 端口被占 main return {rc}, 正式证据保留(未被清理)")
    finally:
        occupant.terminate()
        occupant.wait(timeout=5)

    print("SELFCHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
