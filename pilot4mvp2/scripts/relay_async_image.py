#!/usr/bin/env python3
"""65535 中转站原生异步图片任务客户端（POST /v1/tasks + 轮询 + 下载）。

背景：同步 /images/edits 在该站受不稳定的网关等待窗口影响
（2026-08-18 实测 107s/114s 两次被掐、170s 一次通过），异步任务制
短连接轮询不受影响，且带幂等键（重试不重复扣费）与结构化错误。

配置优先级：
  1. --base-url / --api-key 参数
  2. 环境变量 S65535_BASE_URL / S65535_API_KEY 或 IMAGES_BASE_URL / IMAGES_API_KEY
  3. pilot4mvp2/.env.local 的 IMAGES_BASE_URL / IMAGES_API_KEY

示例：
  python relay_async_image.py --model gpt-image-2-eco --quality high \
      --size 2048x1536 --ref 概念图v2.png --prompt-file prompt.txt \
      --out 概念图v3.png --idem-key concept-v3-run1

依赖：requests、Pillow（仅下载验证时需要）。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ENV_LOCAL = Path(__file__).resolve().parents[1] / ".env.local"
DEFAULT_BASE = "https://task-api-1-cn.65535.space"
MAX_REFS = 16  # 服务端参考图上限


def load_env_local(path: Path) -> dict[str, str]:
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


def resolve_config(args: argparse.Namespace) -> tuple[str, str]:
    file_env = load_env_local(ENV_LOCAL)
    base = (
        args.base_url
        or os.environ.get("S65535_BASE_URL")
        or os.environ.get("IMAGES_BASE_URL")
        or file_env.get("IMAGES_BASE_URL")
        or DEFAULT_BASE
    ).rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    key = (
        args.api_key
        or os.environ.get("S65535_API_KEY")
        or os.environ.get("IMAGES_API_KEY")
        or file_env.get("IMAGES_API_KEY")
        or ""
    )
    if not key:
        raise SystemExit("缺少 API Key：用 --api-key 或配置 IMAGES_API_KEY/S65535_API_KEY")
    return base, key


def to_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def normalize_ref(ref: str) -> str:
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    path = Path(ref)
    if not path.is_file():
        raise SystemExit(f"参考图不存在：{ref}")
    return to_data_uri(path)


def _error(stage: str, code: str, message: str, *, retryable: bool = False,
           status: int | None = None) -> dict:
    """构造可安全交给控制器的错误；绝不包含响应正文、URL 或凭证。"""
    item = {"stage": stage, "code": code, "message": message,
            "retryable": retryable}
    if status is not None:
        item["http_status"] = status
    return {"ok": False, "error": item}


def _submission_unknown() -> dict:
    return _error(
        "submit",
        "submission_unknown",
        "无法确认任务是否已创建，请人工核对幂等键对应的任务",
    )


def _conflict_task_id(data: object):
    if not isinstance(data, dict):
        return None
    nested_task = data.get("task")
    candidates = (
        data.get("id"),
        data.get("task_id"),
        nested_task.get("id") if isinstance(nested_task, dict) else None,
    )
    return next(
        (value for value in candidates if isinstance(value, str) and value),
        None,
    )


def submit_task(
    session: requests.Session,
    base: str,
    payload: dict,
    idem_key: str,
    retries: int = 3,
    sleep_fn=time.sleep,
    on_created=None,
) -> dict:
    """结构化提交边界；网络失败可用同一幂等键安全重试。"""
    for attempt in range(1, retries + 1):
        try:
            response = session.post(
                f"{base}/v1/tasks", json=payload,
                headers={"Idempotency-Key": idem_key}, timeout=60,
            )
            if response.status_code == 409:
                try:
                    data = response.json()
                except (ValueError, TypeError):
                    return _submission_unknown()
                task_id = _conflict_task_id(data)
                if not task_id:
                    return _submission_unknown()
                if on_created is not None:
                    on_created(task_id, data)
                return {"ok": True, "stage": "submit", "task": data, "task_id": task_id}
            if response.status_code >= 400:
                return _error("submit", "http_error", "任务提交被服务端拒绝",
                              retryable=response.status_code >= 500,
                              status=response.status_code)
            try:
                data = response.json()
            except (ValueError, TypeError):
                return _submission_unknown()
            task_id = data.get("id") if isinstance(data, dict) else None
            if not task_id:
                return _submission_unknown()
            if on_created is not None:
                on_created(task_id, data)
            return {"ok": True, "stage": "submit", "task": data, "task_id": task_id}
        except requests.RequestException:
            if attempt < retries:
                sleep_fn(2 * attempt)
                continue
            return _submission_unknown()
    return _submission_unknown()


def poll_task(session: requests.Session, base: str, task_id: str, timeout_s: float,
              sleep_fn=time.sleep, clock=time.monotonic, status_fn=print) -> dict:
    """结构化轮询边界，可在提交后单独调用，也可断线恢复。"""
    deadline = clock() + timeout_s
    interval = 2.0
    while clock() < deadline:
        try:
            response = session.get(f"{base}/v1/tasks/{task_id}", timeout=30)
            if response.status_code >= 400:
                return _error("poll", "http_error", "任务状态查询失败",
                              retryable=response.status_code >= 500,
                              status=response.status_code)
            task = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return _error("poll", "network_error", "任务状态查询网络异常，可稍后恢复轮询",
                          retryable=True)
        if not isinstance(task, dict):
            return _error("poll", "invalid_response", "任务状态响应格式无效")
        status = task.get("status")
        if status == "done":
            return {"ok": True, "stage": "poll", "task": task, "task_id": task_id}
        if status == "failed":
            return _error("poll", "provider_failed", "图片任务由服务提供方标记为失败")
        status_fn(f"[{time.strftime('%H:%M:%S')}] {status} …", flush=True)
        sleep_fn(min(interval, max(0, deadline - clock())))
        interval = min(interval * 1.25, 5.0)
    return _error("poll", "timeout", "任务轮询超时，可稍后使用 task_id 恢复", retryable=True)


def download_results(
    session: requests.Session,
    task: dict,
    task_id: str,
    args_out: str | None = None,
    download_session: requests.Session | None = None,
) -> dict:
    """结构化下载与图片解码验证边界。

    Result URLs are signed object-storage URLs and must not receive the API
    Authorization header. Callers with an authenticated session should pass a
    separate unauthenticated session explicitly.
    """
    urls = task.get("result_urls") or []
    if not urls:
        return _error("download", "missing_result_urls", "任务完成但没有结果地址")
    try:
        from PIL import Image
        saved = []
        downloader = download_session or requests.Session()
        for i, url in enumerate(urls):
            out = output_path(args_out, task_id, i, len(urls))
            out.parent.mkdir(parents=True, exist_ok=True)
            response = downloader.get(url, timeout=180)
            if response.status_code >= 400:
                return _error("download", "http_error", "结果下载失败",
                              retryable=response.status_code >= 500,
                              status=response.status_code)
            out.write_bytes(response.content)
            with Image.open(out) as image:
                dims = image.size
                image.load()
            saved.append({"path": str(out), "size": list(dims)})
        return {"ok": True, "stage": "download", "task_id": task_id, "saved": saved}
    except (requests.RequestException, OSError, ValueError):
        return _error("download", "decode_error", "结果下载或图片解码失败")


def safe_task_metadata(task: dict) -> dict:
    """Return auditable provider metadata without signed URLs or response extras."""
    allowed = (
        "id",
        "status",
        "model",
        "quality",
        "size",
        "image_size_tier",
        "cost_usd",
        "created_at",
        "completed_at",
    )
    return {key: task[key] for key in allowed if key in task}


def _legacy_message(result: dict) -> str:
    error = result.get("error", {})
    return f"{error.get('code', 'error')}：{error.get('message', '操作失败')}"


def submit(session: requests.Session, base: str, payload: dict, idem_key: str, retries: int = 3) -> dict:
    result = submit_task(session, base, payload, idem_key, retries)
    if not result["ok"]:
        raise SystemExit(_legacy_message(result))
    return result["task"]


def wait_task(session: requests.Session, base: str, task_id: str, timeout_s: float) -> dict:
    result = poll_task(session, base, task_id, timeout_s)
    if not result["ok"]:
        raise SystemExit(_legacy_message(result))
    return result["task"]


def output_path(args_out: str | None, task_id: str, index: int, total: int) -> Path:
    if args_out:
        if "{i}" in args_out:
            return Path(args_out.format(i=index))
        if total == 1:
            return Path(args_out)
        path = Path(args_out)
        return path.with_name(f"{path.stem}-{index}{path.suffix}")
    return Path(f"{task_id}-{index}.png")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prompt", help="提示词（与 --prompt-file 二选一）")
    parser.add_argument("--prompt-file", help="提示词文件路径（utf-8）")
    parser.add_argument(
        "--ref", action="append", default=[],
        help="参考图：本地路径或 https URL；可重复，最多 16 张",
    )
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", help="像素尺寸（如 2048x1536）或比例（如 4:3）")
    parser.add_argument("--resolution", help="清晰度档位：1k/2k/4k，与比例组合使用")
    parser.add_argument("--quality", help="质量档位，取值由模型/渠道决定")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--out", help="输出路径；可用 {i} 占位，n>1 时自动加 -i 后缀")
    parser.add_argument("--idem-key", default="img-" + uuid.uuid4().hex[:12],
                        help="幂等键；网络失败重试务必复用同一个键")
    parser.add_argument("--timeout", type=float, default=900, help="轮询总超时秒数")
    parser.add_argument("--base-url", help="覆盖 API base URL")
    parser.add_argument("--api-key", help="覆盖 API key")
    parser.add_argument("--show-json", action="store_true", help="结束时打印完整任务 JSON")
    parser.add_argument("--resume-task-id", help="跳过提交，直接恢复已有 task_id 的轮询")
    parser.add_argument("--result-json", help="写入不含凭证的结构化结果 JSON")
    args = parser.parse_args()

    if not args.resume_task_id and not args.prompt and not args.prompt_file:
        parser.error("需要 --prompt 或 --prompt-file（恢复任务时可省略）")
    if len(args.ref) > MAX_REFS:
        parser.error(f"参考图最多 {MAX_REFS} 张")

    base, key = resolve_config(args)
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}"})

    if args.resume_task_id:
        task_id = args.resume_task_id
        print(f"恢复任务：id={task_id}")
    else:
        prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8")
        task_input: dict = {"prompt": prompt}
        if args.size:
            task_input["size"] = args.size
        if args.resolution:
            task_input["resolution"] = args.resolution
        if args.quality:
            task_input["quality"] = args.quality
        if args.n != 1:
            task_input["n"] = args.n
        if args.ref:
            task_input["image_urls"] = [normalize_ref(r) for r in args.ref]
        payload = {"kind": "image", "model": args.model, "input": task_input}
        submitted = submit_task(session, base, payload, args.idem_key)
        if not submitted["ok"]:
            raise SystemExit(_legacy_message(submitted))
        task_id = submitted["task_id"]
        print(f"任务已提交：id={task_id}  idem_key={args.idem_key}")

    polled = poll_task(session, base, task_id, args.timeout)
    if not polled["ok"]:
        if args.result_json:
            Path(args.result_json).write_text(json.dumps(polled, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(_legacy_message(polled))
    task = polled["task"]
    downloaded = download_results(
        session, task, task_id, args.out, download_session=requests.Session()
    )
    if args.result_json:
        safe_result = {
            "poll": {
                "ok": True,
                "stage": "poll",
                "task_id": task_id,
                "task": safe_task_metadata(task),
            },
            "download": downloaded,
        }
        Path(args.result_json).write_text(
            json.dumps(safe_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if not downloaded["ok"]:
        raise SystemExit(_legacy_message(downloaded))

    print("status  :", task.get("status"))
    print("model   :", task.get("model"), " quality:", task.get("quality", ""))
    print("size    :", task.get("size"), task.get("image_size_tier", ""))
    print("cost_usd:", task.get("cost_usd"))
    for item in downloaded["saved"]:
        print("saved   :", item["path"], tuple(item["size"]))
    if args.show_json:
        print(json.dumps(safe_task_metadata(task), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
