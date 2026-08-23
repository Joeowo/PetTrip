"""简化的文件存储辅助类，用于测试（T7 集成测试修复）。"""

from pathlib import Path
from typing import NamedTuple


class StoredFile(NamedTuple):
    """存储的文件信息。"""
    file_id: str
    content: bytes
    mime_type: str
    rel_path: str


class SimpleFileStorage:
    """简化的文件存储，用于测试。

    提供 write() 和 read() 方法，兼容工作流中的文件存储接口。
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, StoredFile] = {}

    def write(self, file_id: str, content: bytes, mime_type: str) -> str:
        """存储文件（内存 + 磁盘）。

        Args:
            file_id: 文件 ID
            content: 文件内容（字节）
            mime_type: MIME 类型

        Returns:
            str: 文件 ID（用于后续读取）
        """
        # 存储到磁盘
        file_path = self.base_dir / f"{file_id}.dat"
        file_path.write_bytes(content)

        # 存储到内存索引
        rel_path = file_path.relative_to(self.base_dir).as_posix()
        stored = StoredFile(
            file_id=file_id,
            content=content,
            mime_type=mime_type,
            rel_path=rel_path,
        )
        self._files[file_id] = stored

        return file_id

    def read(self, file_id: str) -> StoredFile:
        """读取文件。

        Args:
            file_id: 文件 ID

        Returns:
            StoredFile: 存储的文件信息

        Raises:
            KeyError: 如果文件不存在
        """
        if file_id not in self._files:
            raise KeyError(f"File not found: {file_id}")

        return self._files[file_id]

    def exists(self, file_id: str) -> bool:
        """检查文件是否存在。"""
        return file_id in self._files
