"""服务配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# agent_service/ 目录（无论在 worktree 还是主工作区都正确）
PILOT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """服务缺少启动所需的非敏感配置。"""


def _load_env_local(path: Path) -> dict[str, str]:
    """读取 KEY=VALUE 形式的本地 env 文件；缺失或格式不符的行跳过。"""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@dataclass(frozen=True)
class Settings:
    """不可变的服务配置快照。密钥只在进程内存中存在。"""

    service_version: str
    host: str
    port: int
    pilot_root: Path
    data_dir: Path
    db_path: Path
    chat_base_url: str
    chat_api_key: str
    chat_model: str
    chat_timeout: float
    chat_temperature: float
    chat_max_tokens: int
    pilot_api_key: str
    worker_poll_interval: float
    max_text_chars: int
    max_upload_bytes: int = 10 * 1024 * 1024
    max_image_dimension: int = 4096
    max_image_pixels: int = 20_000_000
    image_base_url: str = ""
    image_api_key: str = ""
    image_model: str = "gpt-image-2"
    image_timeout: float = 120.0
    image_request_size: str = "1024x1024"
    image_generation_path: str = "/images/generations"
    image_canvas_width: int = 1024
    image_canvas_height: int = 1024
    image_max_decoded_bytes: int = 20 * 1024 * 1024
    image_max_pixels: int = 20_000_000
    image_use_async_tasks: bool = False  # 是否使用异步任务 API（推荐）


def load_settings(
    overrides: dict[str, str] | None = None,
    *,
    require_chat: bool = True,
) -> Settings:
    """合并环境变量、可选 `.env.local` 和显式覆盖项并校验必要配置。"""
    env: dict[str, str] = dict(os.environ)
    for key, value in _load_env_local(PILOT_ROOT / ".env.local").items():
        env.setdefault(key, value)
    if overrides:
        env.update(overrides)

    # 兼容早期试点配置；当前 Chat Provider 仍使用 Chat Completions 协议。
    for chat_key in ("BASE_URL", "API_KEY", "MODEL"):
        env.setdefault(f"CHAT_{chat_key}", env.get(f"RESPONSES_{chat_key}", ""))

    required = ["PILOT_API_KEY"]
    if require_chat:
        required.extend(["CHAT_BASE_URL", "CHAT_API_KEY", "CHAT_MODEL"])
    missing = [key for key in required if not env.get(key, "").strip()]
    if missing:
        raise ConfigurationError(f"缺少必要服务配置：{', '.join(missing)}")

    explicit_images_base_url = env.get("IMAGES_BASE_URL", "").strip()
    explicit_images_api_key = env.get("IMAGES_API_KEY", "").strip()
    if explicit_images_base_url and not explicit_images_api_key:
        raise ConfigurationError(
            "配置 IMAGES_BASE_URL 时必须同时配置 IMAGES_API_KEY。"
        )

    data_dir = PILOT_ROOT / env.get("DATA_DIR", "data")
    db_path = Path(env.get("DB_PATH", str(data_dir / "agent.db")))
    return Settings(
        service_version=env.get("SERVICE_VERSION", "0.1.0"),
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8001")),
        pilot_root=PILOT_ROOT,
        data_dir=data_dir,
        db_path=db_path,
        chat_base_url=env.get("CHAT_BASE_URL", "").rstrip("/"),
        chat_api_key=env.get("CHAT_API_KEY", ""),
        chat_model=env.get("CHAT_MODEL", ""),
        chat_timeout=float(env.get("CHAT_TIMEOUT", "60")),
        chat_temperature=float(env.get("CHAT_TEMPERATURE", "0.7")),
        chat_max_tokens=int(env.get("CHAT_MAX_TOKENS", "1024")),
        pilot_api_key=env["PILOT_API_KEY"],
        worker_poll_interval=float(env.get("WORKER_POLL_INTERVAL", "0.2")),
        max_text_chars=int(env.get("MAX_TEXT_CHARS", "8000")),
        max_upload_bytes=int(env.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
        max_image_dimension=int(env.get("MAX_IMAGE_DIMENSION", "4096")),
        max_image_pixels=int(env.get("MAX_IMAGE_PIXELS", "20000000")),
        image_base_url=env.get("IMAGES_BASE_URL", env.get("CHAT_BASE_URL", "")).rstrip("/"),
        image_api_key=env.get("IMAGES_API_KEY", env.get("CHAT_API_KEY", "")),
        image_model=env.get("IMAGES_MODEL", "gpt-image-2"),
        image_timeout=float(env.get("IMAGE_TIMEOUT", "120")),
        image_request_size=env.get("IMAGE_REQUEST_SIZE", "1024x1024"),
        image_generation_path=env.get("IMAGE_GENERATION_PATH", "/images/generations"),
        image_canvas_width=int(env.get("IMAGE_CANVAS_WIDTH", "1024")),
        image_canvas_height=int(env.get("IMAGE_CANVAS_HEIGHT", "1024")),
        image_max_decoded_bytes=int(
            env.get("IMAGE_MAX_DECODED_BYTES", str(20 * 1024 * 1024))
        ),
        image_max_pixels=int(env.get("IMAGE_MAX_PIXELS", "20000000")),
        image_use_async_tasks=env.get("IMAGE_USE_ASYNC_TASKS", "false").lower() in ("true", "1", "yes"),
    )
