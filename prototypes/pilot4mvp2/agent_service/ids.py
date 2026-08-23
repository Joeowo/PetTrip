"""不透明标识符生成。

服务端生成带有稳定前缀的不透明字符串标识符（spec §5.1），客户端不得从中解析业务
含义。主体使用 ULID 风格的 Crockford Base32 编码（48 位毫秒时间戳 + 80 位随机），
既保证大致按创建时间排序，又保证全局唯一与不可猜测。
"""

from __future__ import annotations

import os
import time

# Crockford Base32 字母表（剔除 I/L/O/U，避免与数字混淆）
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIME_MS_MASK = (1 << 48) - 1


def ulid() -> str:
    """返回 26 字符的 ULID 风格主体（128 位 -> 26 * 5 位）。"""
    ms = int(time.time() * 1000) & _TIME_MS_MASK
    randomness = os.urandom(10)  # 80 位
    value = (ms << 80) | int.from_bytes(randomness, "big")
    chars = [""] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(chars)


def new_id(prefix: str) -> str:
    """返回 ``prefix_<ulid>`` 形式的不透明标识符。"""
    return f"{prefix}_{ulid()}"
