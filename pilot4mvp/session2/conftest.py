"""pytest 配置：把 session2/ 加入 sys.path，使 content_service 可被测试导入。"""

import sys
from pathlib import Path

SESSION2_ROOT = Path(__file__).resolve().parent
if str(SESSION2_ROOT) not in sys.path:
    sys.path.insert(0, str(SESSION2_ROOT))
