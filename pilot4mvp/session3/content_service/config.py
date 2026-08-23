"""会话3 外部模型配置；仅由显式入口加载根目录 .env。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"

RESPONSES_VARIABLES = (
    "RESPONSES_BASE_URL",
    "RESPONSES_API_KEY",
    "RESPONSES_MODEL",
)
IMAGES_VARIABLES = (
    "IMAGES_BASE_URL",
    "IMAGES_API_KEY",
    "IMAGES_MODEL",
)


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str

    def __repr__(self) -> str:
        return (
            f"ProviderConfig(base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key=<hidden>)"
        )


def load_local_env() -> None:
    """显式加载根 .env；shell 中已有变量优先。"""
    load_dotenv(ENV_PATH, override=False)


def missing_variables(names: tuple[str, ...]) -> list[str]:
    """返回空缺变量名，不读取或暴露其他变量值。"""
    return [name for name in names if not os.environ.get(name, "").strip()]


def _provider_config(names: tuple[str, str, str]) -> ProviderConfig:
    missing = missing_variables(names)
    if missing:
        raise RuntimeError("缺少必需环境变量: " + ", ".join(missing))
    return ProviderConfig(
        base_url=os.environ[names[0]].strip(),
        api_key=os.environ[names[1]].strip(),
        model=os.environ[names[2]].strip(),
    )


def responses_config() -> ProviderConfig:
    return _provider_config(RESPONSES_VARIABLES)


def images_config() -> ProviderConfig:
    return _provider_config(IMAGES_VARIABLES)
