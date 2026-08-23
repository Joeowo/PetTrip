"""通过公网 HTTPS 验证已部署 Agent Service 的图片生成与下载。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image


def _load_base_url(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.hostname
        or not parsed.hostname.lower().endswith(".trycloudflare.com")
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Base URL 必须是 Cloudflare Quick Tunnel HTTPS 根地址。")
    return value


def _load_api_key(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value.startswith("pettrip_pilot_") or len(value) < 48 or any(
        character.isspace() for character in value
    ):
        raise ValueError("PetTrip Pilot API Key 格式不安全。")
    return value


def _poll_run(
    client: httpx.Client,
    *,
    base_url: str,
    headers: dict[str, str],
    run_id: str,
    timeout_seconds: float,
) -> tuple[list[str], dict[str, Any]]:
    statuses: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(
            f"{base_url}/api/v1/runs/{run_id}",
            headers=headers,
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise RuntimeError("Run 查询没有返回 200。")
        body = response.json()
        status = body["status"]
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status in {"succeeded", "failed"}:
            return statuses, body
        time.sleep(1)
    raise RuntimeError("图片 Run 未在验收时限内进入终态。")


def verify_public_image_api(
    *,
    base_url_path: Path,
    api_key_path: Path,
    timeout_seconds: float = 180,
) -> dict[str, Any]:
    """创建公网图片 Run，下载两次，并返回不含秘密的校验结果。"""
    base_url = _load_base_url(base_url_path)
    api_key = _load_api_key(api_key_path)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            session_response = client.post(
                f"{base_url}/api/v1/sessions",
                headers=headers,
            )
            session_response.raise_for_status()
            if session_response.status_code != 201:
                raise RuntimeError("Session 创建没有返回 201。")
            session_id = session_response.json()["session_id"]

            create_response = client.post(
                f"{base_url}/api/v1/runs",
                headers={
                    **headers,
                    "Idempotency-Key": f"public-image-{secrets.token_hex(12)}",
                },
                json={
                    "session_id": session_id,
                    "input": {
                        "text": (
                            "生成一张温暖明亮的海边灯塔宠物旅行插画，"
                            "画面中有一只快乐散步的柯基。"
                        ),
                        "attachments": [],
                    },
                    "response_format": {"modalities": ["image"]},
                },
            )
            create_response.raise_for_status()
            if create_response.status_code != 202:
                raise RuntimeError("Run 创建没有返回 202。")
            run_id = create_response.json()["run_id"]
            statuses, terminal = _poll_run(
                client,
                base_url=base_url,
                headers=headers,
                run_id=run_id,
                timeout_seconds=timeout_seconds,
            )
            if terminal["status"] != "succeeded":
                error = terminal.get("error") or {}
                return {
                    "passed": False,
                    "session_id": session_id,
                    "run_id": run_id,
                    "statuses_observed": statuses,
                    "terminal_status": terminal["status"],
                    "error_code": error.get("code"),
                    "retryable": error.get("retryable"),
                }

            attachments = terminal.get("output", {}).get("attachments", [])
            if len(attachments) != 1:
                raise RuntimeError("图片 Run 返回的附件数量不符合契约。")
            attachment = attachments[0]
            if (
                attachment.get("source") != "agent_generated"
                or attachment.get("purpose") != "generated_image"
            ):
                raise RuntimeError("生成图片附件不符合资源契约。")

            download_url = attachment["download_url"]
            if not isinstance(download_url, str) or not download_url.startswith(
                "/api/v1/files/"
            ):
                raise RuntimeError("生成图片下载地址不符合资源契约。")
            first = client.get(f"{base_url}{download_url}", headers=headers)
            second = client.get(f"{base_url}{download_url}", headers=headers)
            first.raise_for_status()
            second.raise_for_status()
            if first.status_code != 200 or second.status_code != 200:
                raise RuntimeError("图片下载没有返回 200。")

        first_hash = hashlib.sha256(first.content).hexdigest()
        second_hash = hashlib.sha256(second.content).hexdigest()
        with Image.open(io.BytesIO(first.content)) as image:
            image.load()
            image_format = image.format
            image_size = list(image.size)
        hashes_match = first_hash == second_hash == attachment["sha256"]
        passed = (
            hashes_match
            and image_format == "PNG"
            and image_size == [1024, 1024]
            and [attachment["width"], attachment["height"]] == [1024, 1024]
            and attachment["mime_type"] == "image/png"
            and attachment["size_bytes"] == len(first.content)
        )
        return {
            "passed": passed,
            "session_id": session_id,
            "run_id": run_id,
            "statuses_observed": statuses,
            "terminal_status": terminal["status"],
            "file_id": attachment["file_id"],
            "source": attachment["source"],
            "purpose": attachment["purpose"],
            "mime_type": attachment["mime_type"],
            "width": attachment["width"],
            "height": attachment["height"],
            "size_bytes": attachment["size_bytes"],
            "metadata_sha256": attachment["sha256"],
            "download_sha256": first_hash,
            "repeat_download_sha256": second_hash,
            "hashes_match": hashes_match,
            "pillow_format": image_format,
            "pillow_size": image_size,
        }
    finally:
        api_key = ""
        headers.clear()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="通过公网 HTTPS 验证 Agent Service 图片生成与下载。"
    )
    parser.add_argument("--base-url-file", required=True, type=Path)
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = verify_public_image_api(
            base_url_path=args.base_url_file,
            api_key_path=args.api_key_file,
            timeout_seconds=args.timeout,
        )
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
