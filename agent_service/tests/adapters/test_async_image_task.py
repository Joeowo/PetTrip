"""测试异步图片生成任务客户端（T7）。"""

from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from PIL import Image

from agent_service.adapters.async_image_task import (
    AsyncImageTaskClient,
    AsyncImageTaskRequest,
)
from agent_service.adapters.image import ImageProviderError


def test_submit_task_success():
    """测试：成功提交任务。"""
    client = AsyncImageTaskClient(
        base_url="https://api.example.com",
        api_key="test-key",
    )

    # Mock HTTP 响应
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"task_id": "task-123"}

    aperture_img = Image.new("RGB", (1024, 1024), color=(100, 150, 200))
    aperture_buffer = BytesIO()
    aperture_img.save(aperture_buffer, format="PNG")
    aperture_bytes = aperture_buffer.getvalue()

    mask_img = Image.new("L", (1024, 1024), color=255)
    mask_buffer = BytesIO()
    mask_img.save(mask_buffer, format="PNG")
    mask_bytes = mask_buffer.getvalue()

    request = AsyncImageTaskRequest(
        model="gpt-image-2",
        prompt="Test prompt",
        image_bytes=aperture_bytes,
        mask_bytes=mask_bytes,
        size="1024x1024",
        idempotency_key="test-key-123",
    )

    with patch("httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        task_id = client._submit_task(request)

        assert task_id == "task-123"
        assert mock_client.post.called


def test_submit_task_idempotent_conflict():
    """测试：幂等冲突（409）处理。"""
    client = AsyncImageTaskClient(
        base_url="https://api.example.com",
        api_key="test-key",
    )

    # Mock 409 响应
    mock_response = Mock()
    mock_response.status_code = 409
    mock_response.json.return_value = {"task_id": "existing-task-456"}

    aperture_bytes = b"fake-aperture"
    mask_bytes = b"fake-mask"

    request = AsyncImageTaskRequest(
        model="gpt-image-2",
        prompt="Test prompt",
        image_bytes=aperture_bytes,
        mask_bytes=mask_bytes,
        size="1024x1024",
        idempotency_key="duplicate-key",
    )

    with patch("httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        task_id = client._submit_task(request)

        # 应该返回已存在的任务 ID
        assert task_id == "existing-task-456"


def test_poll_until_complete_success():
    """测试：轮询直到任务完成。"""
    client = AsyncImageTaskClient(
        base_url="https://api.example.com",
        api_key="test-key",
        poll_interval=0.01,  # 快速轮询用于测试
    )

    # Mock 响应序列：pending → running → completed
    mock_responses = [
        Mock(status_code=200, json=lambda: {"status": "pending"}),
        Mock(status_code=200, json=lambda: {"status": "running"}),
        Mock(
            status_code=200,
            json=lambda: {
                "status": "completed",
                "outputs": {
                    "images": [{"data": "fake-base64-data"}]
                },
            },
        ),
    ]

    with patch("httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.get.side_effect = mock_responses
        mock_client_class.return_value = mock_client

        result = client._poll_until_complete("task-123")

        assert result["status"] == "completed"
        assert mock_client.get.call_count == 3


def test_poll_until_complete_failed():
    """测试：任务失败处理。"""
    client = AsyncImageTaskClient(
        base_url="https://api.example.com",
        api_key="test-key",
    )

    mock_response = Mock(
        status_code=200,
        json=lambda: {
            "status": "failed",
            "error": {"message": "生成失败"}
        },
    )

    with patch("httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(ImageProviderError, match="任务失败: 生成失败"):
            client._poll_until_complete("task-123")


def test_generate_idempotency_key():
    """测试：幂等键生成。"""
    from agent_service.domain.scene_image_generation import generate_idempotency_key

    # 相同输入生成相同的键
    key1 = generate_idempotency_key(
        scene_id="scene-abc-123",
        aperture_sha256="abc123def456",
        pet_behavior="四处张望",
        pet_emotion="好奇",
    )

    key2 = generate_idempotency_key(
        scene_id="scene-abc-123",
        aperture_sha256="abc123def456",
        pet_behavior="四处张望",
        pet_emotion="好奇",
    )

    assert key1 == key2
    assert key1.startswith("scene-scene-ab")

    # 不同输入生成不同的键
    key3 = generate_idempotency_key(
        scene_id="scene-abc-123",
        aperture_sha256="different-hash",
        pet_behavior="四处张望",
        pet_emotion="好奇",
    )

    assert key3 != key1
