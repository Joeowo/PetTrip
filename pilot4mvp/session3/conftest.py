"""让会话3 测试从本目录导入 content_service。"""

from __future__ import annotations

import sys
from pathlib import Path

SESSION3_ROOT = Path(__file__).resolve().parent
if str(SESSION3_ROOT) not in sys.path:
    sys.path.insert(0, str(SESSION3_ROOT))
