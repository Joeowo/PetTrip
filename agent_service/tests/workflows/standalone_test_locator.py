"""独立工作流测试 - 验证场景定位工作流。

运行方式：python tests/workflows/standalone_test_locator.py
"""

import sys
from pathlib import Path

# Add agent_service to path
agent_service_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(agent_service_root))

import hashlib
import tempfile
from io import BytesIO
from PIL import Image

from storage.destination_storage import DestinationRepository
from storage.files import LocalImageStorage
from workflows.scene_locator import run_scene_locator_workflow
from shared.ids import new_id


def create_mock_environment(repo, file_storage, destination_id):
    """创建 mock 共享环境制品。"""
    # 生成测试环境图
    width, height = 2048, 1152
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            r = int(135 + (x / width) * 50)
            g = int(206 + (y / height) * 30)
            b = int(235 - (x / width) * 50)
            pixels[x, y] = (r, g, b)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    # 存储文件
    file_id = new_id()
    file_storage.write(file_id, image_bytes, "image/png")

    # 创建共享环境制品
    shared_env_id = new_id()
    sha256 = hashlib.sha256(image_bytes).hexdigest()

    repo.create_shared_environment_artifact(
        shared_environment_id=shared_env_id,
        destination_id=destination_id,
        source_run_id=new_id(),
        image_file_id=file_id,
        image_sha256=sha256,
        width_px=width,
        height_px=height,
        prompt_snapshot_id=new_id(),
    )

    return shared_env_id


def test_workflow_success():
    """测试：定位工作流成功检测圆心。"""
    print("\n[Test 1] Workflow success...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalImageStorage(base_dir=Path(tmpdir))
        repo = DestinationRepository(db_path=str(Path(tmpdir) / "test.db"))

        destination_id = new_id()
        scene_id = new_id()

        # 创建共享环境
        shared_env_id = create_mock_environment(repo, storage, destination_id)

        # 运行定位工作流
        result = run_scene_locator_workflow(
            destination_id=destination_id,
            scene_id=scene_id,
            shared_environment_id=shared_env_id,
            semantic_anchor="木屋前道路内侧平地",
            interaction_diameter_px=160,
            repo=repo,
            file_storage=storage,
        )

        # 验证
        assert result["error"] is None, f"Workflow failed: {result['error']}"
        assert result["planned_center_x"] is not None
        assert result["planned_center_y"] is not None
        assert isinstance(result["planned_center_x"], int)
        assert isinstance(result["planned_center_y"], int)
        assert 0 <= result["planned_center_x"] < 2048
        assert 0 <= result["planned_center_y"] < 1152

        print(f"  PASS - Center: ({result['planned_center_x']}, {result['planned_center_y']})")


def test_max_attempts():
    """测试：最多 3 attempts。"""
    print("\n[Test 2] Max 3 attempts configuration...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalImageStorage(base_dir=Path(tmpdir))
        repo = DestinationRepository(db_path=str(Path(tmpdir) / "test.db"))

        destination_id = new_id()
        scene_id = new_id()

        shared_env_id = create_mock_environment(repo, storage, destination_id)

        result = run_scene_locator_workflow(
            destination_id=destination_id,
            scene_id=scene_id,
            shared_environment_id=shared_env_id,
            semantic_anchor="测试锚点",
            interaction_diameter_px=160,
            repo=repo,
            file_storage=storage,
        )

        assert result["max_attempts"] == 3
        print(f"  PASS - Max attempts: {result['max_attempts']}")


def test_inputs_preserved():
    """测试：重试保持输入不变。"""
    print("\n[Test 3] Inputs preserved during retry...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalImageStorage(base_dir=Path(tmpdir))
        repo = DestinationRepository(db_path=str(Path(tmpdir) / "test.db"))

        destination_id = new_id()
        scene_id = new_id()
        semantic_anchor = "固定语义锚点"

        shared_env_id = create_mock_environment(repo, storage, destination_id)

        result = run_scene_locator_workflow(
            destination_id=destination_id,
            scene_id=scene_id,
            shared_environment_id=shared_env_id,
            semantic_anchor=semantic_anchor,
            interaction_diameter_px=160,
            repo=repo,
            file_storage=storage,
        )

        assert result["destination_id"] == destination_id
        assert result["scene_id"] == scene_id
        assert result["shared_environment_id"] == shared_env_id
        assert result["semantic_anchor"] == semantic_anchor
        assert result["environment_file_id"] is not None
        assert result["environment_sha256"] is not None

        print("  PASS - All inputs preserved")


def test_no_fallback():
    """测试：禁止兜底。"""
    print("\n[Test 4] No fallback coordinates...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalImageStorage(base_dir=Path(tmpdir))
        repo = DestinationRepository(db_path=str(Path(tmpdir) / "test.db"))

        destination_id = new_id()
        scene_id = new_id()

        shared_env_id = create_mock_environment(repo, storage, destination_id)

        result = run_scene_locator_workflow(
            destination_id=destination_id,
            scene_id=scene_id,
            shared_environment_id=shared_env_id,
            semantic_anchor="测试",
            interaction_diameter_px=160,
            repo=repo,
            file_storage=storage,
        )

        # 如果失败，不应该有坐标
        if result["error"] is not None:
            assert result["planned_center_x"] is None
            assert result["planned_center_y"] is None
            print("  PASS - No fallback on failure")
        else:
            print("  PASS - Success (no fallback check needed)")


def test_diagnostics():
    """测试：诊断信息完整。"""
    print("\n[Test 5] Diagnostics available...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalImageStorage(base_dir=Path(tmpdir))
        repo = DestinationRepository(db_path=str(Path(tmpdir) / "test.db"))

        destination_id = new_id()
        scene_id = new_id()

        shared_env_id = create_mock_environment(repo, storage, destination_id)

        result = run_scene_locator_workflow(
            destination_id=destination_id,
            scene_id=scene_id,
            shared_environment_id=shared_env_id,
            semantic_anchor="诊断测试",
            interaction_diameter_px=160,
            repo=repo,
            file_storage=storage,
        )

        assert "detection_diagnostics" in result

        if result["error"] is None:
            diagnostics = result["detection_diagnostics"]
            assert "algorithm" in diagnostics
            assert "planned_locator_center" in diagnostics

        print("  PASS - Diagnostics present")


if __name__ == "__main__":
    print("=" * 70)
    print("Scene Locator Workflow Tests (Issue #18)")
    print("=" * 70)

    tests = [
        test_workflow_success,
        test_max_attempts,
        test_inputs_preserved,
        test_no_fallback,
        test_diagnostics,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  FAIL - {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("\nAll tests passed!")
        sys.exit(0)
    else:
        print(f"\n{failed} tests failed")
        sys.exit(1)
