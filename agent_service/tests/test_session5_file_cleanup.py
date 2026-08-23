from __future__ import annotations

from pathlib import Path

from agent_service.storage.files import LocalImageStorage


def test_startup_cleanup_removes_orphans_and_service_temp_files(tmp_path: Path) -> None:
    storage = LocalImageStorage(tmp_path / "data")
    tracked = storage.store_bytes("file_tracked", b"tracked")
    orphan = storage.store_bytes("file_orphan", b"orphan")
    input_dir = storage.resolve("files/input")
    generated_dir = storage.resolve("files/generated")
    service_temp = input_dir / ".file_temp-123.tmp"
    service_temp.write_bytes(b"partial")
    unrelated_hidden = generated_dir / ".keep"
    unrelated_hidden.write_bytes(b"unrelated")

    removed = storage.remove_untracked_files({tracked})

    assert removed == 2
    assert storage.resolve(tracked).is_file()
    assert not storage.resolve(orphan).exists()
    assert not service_temp.exists()
    assert unrelated_hidden.is_file()
