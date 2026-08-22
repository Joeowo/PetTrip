import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


SCRIPT = Path(__file__).parents[1] / "scripts" / "relay_async_image.py"
spec = importlib.util.spec_from_file_location("relay_async_image", SCRIPT)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class FakeResponse:
    def __init__(self, status_code=200, data=None, content=b""):
        self.status_code = status_code
        self._data = data
        self.content = content

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    def __init__(self, posts=None, gets=None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        result = self.posts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        result = self.gets.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_submit_task_returns_task_id_and_payload():
    session = FakeSession(posts=[FakeResponse(data={"id": "task-1", "status": "queued"})])
    created = []

    result = relay.submit_task(
        session,
        "https://example.test",
        {"kind": "image"},
        "idem-1",
        on_created=lambda task_id, task: created.append((task_id, task["status"])),
    )

    assert result == {"ok": True, "stage": "submit", "task": {"id": "task-1", "status": "queued"}, "task_id": "task-1"}
    assert created == [("task-1", "queued")]
    assert len(session.post_calls) == 1
    assert session.post_calls[0][1]["headers"] == {"Idempotency-Key": "idem-1"}


def test_resume_poll_does_not_post():
    session = FakeSession(gets=[FakeResponse(data={"id": "task-1", "status": "done", "result_urls": []})])

    result = relay.poll_task(session, "https://example.test", "task-1", 1, sleep_fn=lambda _: None)

    assert result["ok"] is True
    assert session.post_calls == []
    assert len(session.get_calls) == 1


def test_provider_failed_is_structured_and_safe():
    session = FakeSession(gets=[FakeResponse(data={"status": "failed", "error_message": "secret provider detail"})])

    result = relay.poll_task(session, "https://example.test", "task-1", 1)

    assert result == {
        "ok": False,
        "error": {
            "stage": "poll",
            "code": "provider_failed",
            "message": "图片任务由服务提供方标记为失败",
            "retryable": False,
        },
    }
    assert "secret" not in str(result)


def test_poll_timeout_is_structured():
    session = FakeSession(gets=[FakeResponse(data={"status": "running"})])
    clock_values = iter([0, 2])

    result = relay.poll_task(
        session, "https://example.test", "task-1", 1,
        sleep_fn=lambda _: None, clock=lambda: next(clock_values), status_fn=lambda *a, **k: None,
    )

    assert result["error"]["code"] == "timeout"
    assert result["error"]["retryable"] is True


def test_download_decode_error_is_structured(tmp_path):
    session = FakeSession(gets=[FakeResponse(content=b"not-an-image")])

    result = relay.download_results(
        session,
        {"result_urls": ["https://signed.example/result"]},
        "task-1",
        str(tmp_path / "out.png"),
        download_session=session,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "decode_error"
    assert "signed.example" not in str(result)


def test_legacy_submit_and_wait_task_remain_usable():
    session = FakeSession(
        posts=[FakeResponse(data={"id": "task-1"})],
        gets=[FakeResponse(data={"id": "task-1", "status": "done"})],
    )

    assert relay.submit(session, "https://example.test", {}, "idem") == {"id": "task-1"}
    assert relay.wait_task(session, "https://example.test", "task-1", 1) == {"id": "task-1", "status": "done"}


def test_safe_task_metadata_excludes_signed_result_urls():
    metadata = relay.safe_task_metadata(
        {
            "id": "task-1",
            "status": "done",
            "cost_usd": 0.1,
            "result_urls": ["https://signed.example/secret"],
            "expires_at": "soon",
        }
    )

    assert metadata == {"id": "task-1", "status": "done", "cost_usd": 0.1}
    assert "signed.example" not in str(metadata)


def test_download_uses_unauthenticated_session_for_signed_url(tmp_path):
    authenticated = FakeSession()
    downloader = FakeSession(gets=[FakeResponse(content=b"not-an-image")])
    authenticated.headers = {"Authorization": "Bearer SECRET"}

    result = relay.download_results(
        authenticated,
        {"result_urls": ["https://signed.example/result"]},
        "task-1",
        str(tmp_path / "out.png"),
        download_session=downloader,
    )

    assert result["error"]["code"] == "decode_error"
    assert authenticated.get_calls == []
    assert downloader.get_calls
    assert "Authorization" not in downloader.get_calls[0][1].get("headers", {})


def test_network_and_conflict_are_structured():
    network = FakeSession(posts=[requests.ConnectionError("secret url")])
    result = relay.submit_task(network, "https://example.test", {}, "idem", retries=1)
    assert result["error"]["code"] == "network_error"
    assert "secret" not in str(result)

    conflict = FakeSession(posts=[FakeResponse(status_code=409)])
    result = relay.submit_task(conflict, "https://example.test", {}, "idem")
    assert result["error"]["code"] == "conflict"
    assert result["error"]["http_status"] == 409
