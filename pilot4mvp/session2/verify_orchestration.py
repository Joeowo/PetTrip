"""验证 run_unity_session2 的防假通过逻辑(不跑 Unity, 快速)。

覆盖:
1. find_session2_result 能从真实 XML 定位会话2 测试并读到 Passed;
2. _port_open 在端口空闲时返回 False;
3. 端口被占用时, 编排在启动 Unity 前即以退出码 5 拒绝运行。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from run_unity_session2 import (  # noqa: E402
    _port_open,
    find_session2_result,
    parse_test_summary,
)

PY = sys.executable
EXISTING_XML = ROOT.parent / "runs" / "session2-unity" / "playmode-results.xml"


def main() -> int:
    # 1. 解析真实 XML: 必须定位到会话2 测试且 Passed
    assert EXISTING_XML.exists(), f"缺少真实结果 XML: {EXISTING_XML}"
    summary = parse_test_summary(EXISTING_XML)
    session2 = find_session2_result(EXISTING_XML)
    print(f"汇总: {summary}")
    print(f"会话2 测试结果: {session2}")
    assert session2 == "Passed", f"期望会话2 = Passed, 得 {session2}"

    # 1b. 会话2 测试被移除时, find_session2_result 必须返回 None(防"只跑会话1"假通过)
    tree_text = EXISTING_XML.read_text(encoding="utf-8")
    import xml.etree.ElementTree as ET

    only_session1 = ET.ElementTree(ET.fromstring(tree_text.replace("Session2HttpLoadingTests", "X")))
    tmp = ROOT.parent / "runs" / "session2-unity" / "_tmp_only_session1.xml"
    only_session1.write(tmp, encoding="utf-8")
    try:
        assert find_session2_result(tmp) is None, "会话2 测试缺失时应返回 None"
        print("会话2 缺失检测: OK (返回 None)")
    finally:
        tmp.unlink(missing_ok=True)

    # 2. 端口空闲
    assert _port_open("127.0.0.1", 8000) is False, "8000 应空闲"
    print("端口 8000 空闲检测: OK")

    # 3. 端口判定函数: 占用时返回 True。编排 main 据此在清理证据之前 return 5。
    #    直接测试函数本身, 不执行带文件副作用的完整 main()(避免删除验收证据)。
    occupant = subprocess.Popen(
        [PY, "-m", "http.server", "8000"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(2.0)
        assert _port_open("127.0.0.1", 8000) is True, "占用者应让端口可连"
        print("端口被占 _port_open 检测: OK (True -> main 在清理证据前 return 5)")
    finally:
        occupant.terminate()
        occupant.wait(timeout=5)

    print("ORCH_GUARD_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
