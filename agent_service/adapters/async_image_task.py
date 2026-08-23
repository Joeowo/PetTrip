"""异步图片生成任务客户端（支持幂等性和轮询）。

基于 pilot4mvp2/relay_async_image.py 的设计，适配 agent_service 架构。

背景：
- 同步 /images/edits 在某些提供商受网关超时影响
- 异步任务使用短连接轮询，不受长连接窗口限制
- 支持幂等键（重试不重复扣费）
- 提供结构化错误信息
"""

from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from PIL import Image

from agent_service.adapters.image import ImageProviderError, ImageReference, ImageResult


@dataclass
class AsyncImageTaskRequest:
    """异步图片任务请求。"""
    model: str
    prompt: str
    image_bytes: bytes  # 原始图像（aperture）
    mask_bytes: bytes   # Mask 图像
    size: str           # 例如 "2048x1152"
    quality: str = "high"
    n: int = 1
    idempotency_key: str = ""  # 幂等键
    references: tuple[ImageReference, ...] = ()


class AsyncImageTaskClient:
    """异步图片生成任务客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        max_poll_attempts: int = 300,
    ):
        """初始化客户端。

        Args:
            base_url: API 基础 URL
            api_key: API 密钥
            timeout: 单次请求超时（秒）
            poll_interval: 轮询间隔（秒）
            max_poll_attempts: 最大轮询次数
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts

    def submit_and_wait(self, request: AsyncImageTaskRequest) -> ImageResult:
        """提交任务并等待完成。

        Args:
            request: 异步任务请求

        Returns:
            ImageResult: 生成的图像

        Raises:
            ImageProviderError: 任务失败或超时
        """
        # 1. 提交任务
        task_id = self._submit_task(request)

        # 2. 轮询直到完成
        result_data = self._poll_until_complete(task_id)

        # 3. 下载并验证结果
        image_result = self._download_result(result_data)

        return image_result

    def _submit_task(self, request: AsyncImageTaskRequest) -> str:
        """提交异步任务。

        Returns:
            str: 任务 ID

        Raises:
            ImageProviderError: 提交失败
        """
        # 构建任务 payload
        payload = {
            "type": "image.edit",
            "model": request.model,
            "parameters": {
                "prompt": request.prompt,
                "size": request.size,
                "quality": request.quality,
                "n": request.n,
            },
            "inputs": {
                "image": {
                    "type": "base64",
                    "data": base64.b64encode(request.image_bytes).decode("ascii"),
                },
                "mask": {
                    "type": "base64",
                    "data": base64.b64encode(request.mask_bytes).decode("ascii"),
                },
            },
        }
        if request.references:
            payload["inputs"]["references"] = [
                {
                    "role": reference.role,
                    "file_id": reference.file_id,
                    "mime_type": reference.mime_type,
                    "width": reference.width,
                    "height": reference.height,
                    "sha256": reference.sha256,
                    "order_index": reference.order_index,
                    "data": base64.b64encode(reference.data).decode("ascii"),
                }
                for reference in sorted(
                    request.references,
                    key=lambda item: (item.order_index, item.role, item.file_id),
                )
            ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        if request.idempotency_key:
            headers["Idempotency-Key"] = request.idempotency_key

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/v1/tasks",
                    json=payload,
                    headers=headers,
                )

                # 处理幂等冲突（409）
                if response.status_code == 409:
                    data = response.json()
                    task_id = data.get("task_id") or data.get("id")
                    if task_id:
                        return task_id
                    raise ImageProviderError("任务已存在但无法获取 task_id")

                response.raise_for_status()
                data = response.json()
                task_id = data.get("task_id") or data.get("id")

                if not task_id:
                    raise ImageProviderError("服务返回无效的任务 ID")

                return task_id

        except httpx.HTTPError as e:
            raise ImageProviderError(f"提交任务失败: {e}") from e
        except (ValueError, KeyError) as e:
            raise ImageProviderError(f"解析响应失败: {e}") from e

    def _poll_until_complete(self, task_id: str) -> dict[str, Any]:
        """轮询任务直到完成。

        Args:
            task_id: 任务 ID

        Returns:
            dict: 任务结果数据

        Raises:
            ImageProviderError: 任务失败或超时
        """
        for attempt in range(self.max_poll_attempts):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(
                        f"{self.base_url}/v1/tasks/{task_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                    data = response.json()

                status = data.get("status")

                if status == "completed":
                    return data

                if status in ("failed", "cancelled"):
                    error_msg = data.get("error", {}).get("message", "任务失败")
                    raise ImageProviderError(f"任务失败: {error_msg}")

                # 状态为 pending 或 running，继续轮询
                time.sleep(self.poll_interval)

            except httpx.HTTPError as e:
                raise ImageProviderError(f"轮询任务失败: {e}") from e
            except (ValueError, KeyError) as e:
                raise ImageProviderError(f"解析响应失败: {e}") from e

        raise ImageProviderError(f"任务超时（{self.max_poll_attempts} 次轮询后仍未完成）")

    def _download_result(self, result_data: dict[str, Any]) -> ImageResult:
        """下载并验证结果图像。

        Args:
            result_data: 任务结果数据

        Returns:
            ImageResult: 图像结果

        Raises:
            ImageProviderError: 下载或验证失败
        """
        try:
            # 提取结果 URL 或 base64 数据
            outputs = result_data.get("outputs", {})
            images = outputs.get("images", [])

            if not images:
                raise ImageProviderError("任务结果中没有图像")

            image_data = images[0]

            # 支持两种格式：URL 或 base64
            if "url" in image_data:
                # 下载 URL
                image_bytes = self._download_from_url(image_data["url"])
            elif "data" in image_data:
                # 解码 base64
                image_bytes = base64.b64decode(image_data["data"])
            else:
                raise ImageProviderError("图像数据格式不支持")

            # 验证图像
            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size
            img.verify()

            return ImageResult(
                data=image_bytes,
                mime_type="image/png",
                width=width,
                height=height,
            )

        except (KeyError, ValueError, IndexError) as e:
            raise ImageProviderError(f"解析结果失败: {e}") from e

    def _download_from_url(self, url: str) -> bytes:
        """从 URL 下载图像。

        Args:
            url: 图像 URL

        Returns:
            bytes: 图像字节

        Raises:
            ImageProviderError: 下载失败
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as e:
            raise ImageProviderError(f"下载图像失败: {e}") from e
