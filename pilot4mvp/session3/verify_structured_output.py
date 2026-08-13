"""真实结构化 WorldSpec 前置探针；失败时停止，不调用 Images。"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from content_service.config import load_local_env, responses_config
from content_service.evidence import provider_response_evidence, write_json
from content_service.external_models import CallRecord, ProviderCallError, StructuredOutputProvider

SESSION3_ROOT = Path(__file__).resolve().parent
RUNS_DIR = SESSION3_ROOT.parent / "runs"


def _run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return RUNS_DIR / f"session3-preflight-{stamp}-{secrets.token_hex(2)}"


def _write_call(run_dir: Path, name: str, call: CallRecord, api_key: str) -> None:
    write_json(
        run_dir / "external" / f"{name}-call.redacted.json",
        {
            "endpoint": call.endpoint,
            "method": call.method,
            "http_status": call.http_status,
            "request_id": call.request_id,
            "request": call.request,
            "response": provider_response_evidence(call.response, call.request_id),
        },
        api_key,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证真实 Structured Outputs 前置条件")
    parser.add_argument(
        "--allow-chat-compat",
        action="store_true",
        help="明确允许 /responses 不支持时使用 Chat Completions json_schema 适配",
    )
    args = parser.parse_args(argv)

    load_local_env()
    try:
        config = responses_config()
    except RuntimeError as exc:
        print(f"前置条件未满足: {exc}", file=sys.stderr)
        return 2

    run_dir = _run_dir()
    provider = StructuredOutputProvider(
        config,
        allow_chat_compat=args.allow_chat_compat,
    )
    try:
        result = provider.generate_world_spec()
    except ProviderCallError as exc:
        write_json(run_dir / "failure.json", exc.failure, config.api_key)
        for name, call in exc.calls.items():
            _write_call(run_dir, name, call, config.api_key)
        print(
            "结构化输出前置探针失败: "
            f"stage={exc.failure.stage} category={exc.failure.category} "
            f"status={exc.failure.http_status} request_id={exc.failure.request_id} "
            f"evidence={run_dir}",
            file=sys.stderr,
        )
        return 1

    write_json(run_dir / "world-spec.json", result.world_spec, config.api_key)
    write_json(run_dir / "structured-output-evidence.json", result.evidence, config.api_key)
    for name, call in result.calls.items():
        _write_call(run_dir, name, call, config.api_key)

    print(
        "STRUCTURED_OUTPUT_PREFLIGHT_OK "
        f"api={result.evidence.structured_output_api} "
        f"responses_passed={result.evidence.responses_passed} evidence={run_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
