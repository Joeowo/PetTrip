"""会话4 测试共享夹具：真实会话3 run 作为上游 artifact 源。"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from content_service.run_store import RunStore
from content_service.server import create_app

SESSION4_ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN_DIR = SESSION4_ROOT.parent / "runs" / "session3-20260815-023543-bcb8"
DEFAULT_INPUT = "生成一个横向 2D 海边场景，包含一座灯塔；宠物可以在灯塔前挥手；右侧可以放置一个小窝；不要出现车辆。"


@pytest.fixture
def source_run_dir() -> Path:
    assert SOURCE_RUN_DIR.is_dir(), f"会话3 真实 run 缺失: {SOURCE_RUN_DIR}"
    assert (SOURCE_RUN_DIR / "world-spec.json").is_file()
    assert (SOURCE_RUN_DIR / "assets").is_dir()
    return SOURCE_RUN_DIR


class Env:
    """一次服务生命周期的状态目录、SQLite 与客户端。"""

    def __init__(self, state_dir: Path, db_path: Path) -> None:
        self.state_dir = state_dir
        self.db_path = db_path
        self.store = RunStore(state_dir, db_path)

    def start(self, run_id: str | None = None) -> TestClient:
        app = create_app(
            source_run_dir=SOURCE_RUN_DIR,
            state_dir=self.state_dir,
            db_path=self.db_path,
            run_id=run_id,
        )
        return TestClient(app)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path / "runs", tmp_path / "content-service.sqlite3")


@pytest.fixture
def accepted_client(env: Env) -> TestClient:
    """已通过统一输入的客户端（活动 run: session4-test-run）。"""
    client = env.start()
    response = client.post("/runs", json={"run_id": "session4-test-run", "input": DEFAULT_INPUT})
    assert response.status_code == 201, response.text
    return client


def make_png_base64(width: int = 8, height: int = 6, color=(255, 0, 0)) -> str:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def snapshot_payload(client: TestClient) -> dict:
    return client.get("/snapshot").json()


def v2_payload_with_shelter(client: TestClient) -> dict:
    """从当前快照构造合法 v2：仅添加 placed_prefab 并升版本。"""
    payload = snapshot_payload(client)
    payload["schema_version"] = "0.2"
    payload["build_slots"][0]["placed_prefab"] = "small_shelter"
    return payload
