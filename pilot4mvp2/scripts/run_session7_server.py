"""以会话 7 的隔离配置启动本地 Agent Service。"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

import subprocess

import uvicorn

from pilot4mvp2.agent_service.app import create_app
from pilot4mvp2.agent_service.config import ConfigurationError, load_settings

PILOT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PILOT_ROOT.parent
KEY_PREFIX = "pettrip_pilot_"
MINIMUM_KEY_LENGTH = 48


def resolve_external_path(value: Path, name: str) -> Path:
    """要求配置使用仓库外的绝对路径。"""
    if not value.is_absolute():
        raise ValueError(f"{name} 必须是仓库外的绝对路径。")
    resolved = value.expanduser().resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved
    raise ValueError(f"{name} 必须是仓库外的绝对路径。")


def protect_private_directory(path: Path) -> None:
    """在 Windows 上清空原 ACL，只授予当前用户和 SYSTEM。"""
    if os.name != "nt":
        return
    script = (
        "$ErrorActionPreference='Stop';"
        "$path=$env:PETTRIP_PRIVATE_DIRECTORY;"
        "if([string]::IsNullOrWhiteSpace($path)){throw 'Missing private directory.'};"
        "$acl=Get-Acl -LiteralPath $path;"
        "$acl.SetAccessRuleProtection($true,$false);"
        "@($acl.Access)|ForEach-Object{$acl.RemoveAccessRuleSpecific($_)};"
        "$inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit';"
        "$prop=[System.Security.AccessControl.PropagationFlags]::None;"
        "$type=[System.Security.AccessControl.AccessControlType]::Allow;"
        "$rights=[System.Security.AccessControl.FileSystemRights]::FullControl;"
        "$current=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "$acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($current,$rights,$inherit,$prop,$type));"
        "$acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new('SYSTEM',$rights,$inherit,$prop,$type));"
        "Set-Acl -LiteralPath $path -AclObject $acl"
    )
    child_env = os.environ.copy()
    child_env["PETTRIP_PRIVATE_DIRECTORY"] = str(path)
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=child_env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigurationError("无法保护会话 7 私密目录。") from exc


def _default_local_root() -> Path:
    configured = os.environ.get("PETTRIP_SESSION7_LOCAL_ROOT", "").strip()
    if configured:
        return resolve_external_path(Path(configured), "PETTRIP_SESSION7_LOCAL_ROOT")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise ConfigurationError("缺少 LOCALAPPDATA，无法保存本地 Pilot 配置。")
    return resolve_external_path(
        Path(local_app_data) / "PetTrip" / "AgentService" / "Session7",
        "会话 7 本地根目录",
    )


def _is_valid_pilot_key(value: str) -> bool:
    return (
        value.startswith(KEY_PREFIX)
        and len(value) >= MINIMUM_KEY_LENGTH
        and not any(character.isspace() for character in value)
    )


def load_or_create_pilot_key(path: Path) -> str:
    """在仓库外生成或复用高熵 PetTrip Pilot API Key。"""
    path = resolve_external_path(path, "PetTrip Pilot API Key 文件")
    path.parent.mkdir(parents=True, exist_ok=True)
    protect_private_directory(path.parent)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if not _is_valid_pilot_key(value):
            raise ValueError("现有 PetTrip Pilot API Key 格式不安全。")
        return value

    path.parent.mkdir(parents=True, exist_ok=True)
    protect_private_directory(path.parent)
    value = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as key_file:
            key_file.write(f"{value}\n")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return value
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        if not _is_valid_pilot_key(existing):
            raise ValueError("现有 PetTrip Pilot API Key 格式不安全。")
        return existing


def runtime_root_for_key(local_root: Path, pilot_key: str) -> Path:
    """为每个 Key 选择独立数据库，确保轮换后旧 Key 不再有效。"""
    fingerprint = hashlib.sha256(pilot_key.encode("utf-8")).hexdigest()[:16]
    return local_root / "runtime" / fingerprint


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise ConfigurationError("PETTRIP_LOCAL_ENV_PATH 指向的文件不存在。")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_provider_url(value: str) -> None:
    """拒绝会让 Provider Key 离开加密连接的 URL。"""
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("会话 7 的模型 Provider 必须使用无凭据的 HTTPS URL。")


def validate_final_provider_settings(settings: object) -> None:
    """校验最终 Settings，阻断进程环境变量绕过。"""
    validate_provider_url(str(settings.chat_base_url))
    validate_provider_url(str(settings.image_base_url))


def _session7_overrides(local_root: Path, pilot_key: str) -> dict[str, str]:
    env_path_value = os.environ.get("PETTRIP_LOCAL_ENV_PATH", "").strip()
    if not env_path_value:
        raise ConfigurationError("缺少 PETTRIP_LOCAL_ENV_PATH。")
    env_path = resolve_external_path(Path(env_path_value), "PETTRIP_LOCAL_ENV_PATH")
    values = _load_env_file(env_path)
    for key in (
        "IMAGES_BASE_URL",
        "IMAGES_API_KEY",
        "IMAGES_MODEL",
        "IMAGE_TIMEOUT",
        "IMAGE_REQUEST_SIZE",
        "IMAGE_GENERATION_PATH",
        "IMAGE_CANVAS_WIDTH",
        "IMAGE_CANVAS_HEIGHT",
        "IMAGE_MAX_DECODED_BYTES",
        "IMAGE_MAX_PIXELS",
    ):
        environment_value = os.environ.get(key, "").strip()
        if environment_value:
            values[key] = environment_value
    for key in (
        "CHAT_BASE_URL",
        "CHAT_API_KEY",
        "CHAT_MODEL",
        "CHAT_TIMEOUT",
        "CHAT_TEMPERATURE",
        "CHAT_MAX_TOKENS",
    ):
        environment_value = os.environ.get(key, "").strip()
        if environment_value:
            values[key] = environment_value

    chat_base_url = values.get("CHAT_BASE_URL", "").strip()
    validate_provider_url(chat_base_url)
    image_base_url = values.get("IMAGES_BASE_URL", "").strip()
    if image_base_url:
        validate_provider_url(image_base_url)

    port = os.environ.get("PETTRIP_SESSION7_PORT", "8001").strip()
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ConfigurationError("PETTRIP_SESSION7_PORT 必须是有效端口。")

    data_dir = runtime_root_for_key(local_root, pilot_key) / "data"
    values.update(
        {
            "PILOT_API_KEY": pilot_key,
            "HOST": "127.0.0.1",
            "PORT": port,
            "DATA_DIR": str(data_dir),
            "DB_PATH": str(data_dir / "agent.db"),
            "SERVICE_VERSION": "0.7.0-session7",
        }
    )
    return values


def main() -> int:
    """启动只接受本机隧道连接的单进程服务。"""
    try:
        local_root = _default_local_root()
        key_path_value = os.environ.get("PETTRIP_PILOT_KEY_PATH", "").strip()
        key_path = (
            Path(key_path_value).expanduser()
            if key_path_value
            else local_root / "pettrip-pilot-api-key.local"
        )
        pilot_key = load_or_create_pilot_key(key_path)
        local_root.mkdir(parents=True, exist_ok=True)
        protect_private_directory(local_root)
        settings = load_settings(
            overrides=_session7_overrides(local_root, pilot_key),
        )
        validate_final_provider_settings(settings)
        settings.data_dir.parent.mkdir(parents=True, exist_ok=True)
        protect_private_directory(settings.data_dir.parent)
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"会话 7 服务未启动：{exc}", file=sys.stderr)
        return 2

    print(
        "PetTrip 会话 7 服务将仅监听本机回环地址；"
        "Pilot Key、Provider 配置和运行目录未输出。"
    )
    uvicorn.run(
        create_app(settings=settings),
        host="127.0.0.1",
        port=settings.port,
        reload=False,
        workers=1,
        access_log=False,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
