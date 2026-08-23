"""显式执行一次真实 Responses + Images 会话3 流水线。"""

from __future__ import annotations

import argparse
import sys

from content_service.config import images_config, load_local_env, responses_config
from content_service.external_models import ProviderCallError
from content_service.pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行 PetTrip 会话3 真实付费流水线")
    parser.add_argument(
        "--confirm-paid",
        action="store_true",
        help="确认本次将发起真实文本和图片模型调用",
    )
    parser.add_argument(
        "--allow-chat-compat",
        action="store_true",
        help="允许 /responses 不支持时使用 Chat Completions json_schema 适配",
    )
    args = parser.parse_args(argv)
    if not args.confirm_paid:
        print("拒绝执行: 必须显式提供 --confirm-paid", file=sys.stderr)
        return 2

    load_local_env()
    try:
        text_config = responses_config()
        image_config = images_config()
    except RuntimeError as exc:
        print(f"前置条件未满足: {exc}", file=sys.stderr)
        return 2

    try:
        run_dir = run_pipeline(
            text_config,
            image_config,
            allow_chat_compat=args.allow_chat_compat,
            confirm_paid=True,
        )
    except ProviderCallError as exc:
        print(
            "会话3 流水线失败: "
            f"stage={exc.failure.stage} category={exc.failure.category} "
            f"status={exc.failure.http_status} request_id={exc.failure.request_id}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"会话3 流水线失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"SESSION3_PIPELINE_OK run_dir={run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
