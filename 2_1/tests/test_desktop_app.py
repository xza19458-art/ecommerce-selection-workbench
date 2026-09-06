from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop_app  # noqa: E402


class _Response:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_make_server_does_not_require_console_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stderr", None)

    server = desktop_app._make_server(0)

    assert server.config.log_config is None


def test_packaged_database_error_uses_exe_instructions(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "database.json"

    message = desktop_app._database_not_ready_message(
        "数据库不存在",
        config_path,
        frozen=True,
    )

    assert str(config_path) in message
    assert ".\\AmazonSelectionWorkbench.exe --init-mysql" in message
    assert ".venv" not in message


def test_wait_for_readiness_accepts_ready_response(monkeypatch) -> None:
    monkeypatch.setattr(
        desktop_app,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            {"ok": True, "data": {"ready": True, "message": "数据库已就绪"}}
        ),
    )

    ready, message = desktop_app.wait_for_readiness("http://127.0.0.1:8000", timeout=1)

    assert ready is True
    assert message == "数据库已就绪"


def test_wait_for_readiness_preserves_backend_failure_message(monkeypatch) -> None:
    times = iter((0.0, 0.1, 2.0))
    monkeypatch.setattr(desktop_app.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(desktop_app.time, "sleep", lambda _seconds: None)

    def unavailable(*_args, **_kwargs):
        body = json.dumps({"ok": False, "message": "存在待执行迁移"}).encode("utf-8")
        raise HTTPError("http://local/api/ready", 503, "Unavailable", {}, BytesIO(body))

    monkeypatch.setattr(desktop_app, "urlopen", unavailable)

    ready, message = desktop_app.wait_for_readiness("http://127.0.0.1:8000", timeout=1)

    assert ready is False
    assert message == "存在待执行迁移"


def test_utility_preflight_runs_before_normal_desktop_start(monkeypatch) -> None:
    import services.deployment_preflight as deployment_preflight

    shown: list[str] = []
    monkeypatch.setattr(
        deployment_preflight,
        "get_deployment_preflight",
        lambda: {
            "message": "分析可用，采集待准备",
            "ready_for_analysis": True,
            "ready_for_collection": False,
        },
    )
    monkeypatch.setattr(
        desktop_app,
        "_message_box_info",
        lambda _window, text: shown.append(text),
    )

    result = desktop_app._run_utility_mode(["--deployment-preflight"])

    assert result == 0
    assert "分析功能：已就绪" in shown[0]
    assert "联网采集：未就绪" in shown[0]


def test_utility_backup_passes_explicit_private_mode(monkeypatch, tmp_path: Path) -> None:
    import services.user_data_transfer as user_data_transfer

    calls: list[dict] = []

    class Summary:
        def to_dict(self):
            return {
                "message": "备份完成",
                "archive_path": str(tmp_path / "backup.zip"),
                "archive_sha256": "A" * 64,
                "file_count": 2,
            }

    monkeypatch.setattr(
        user_data_transfer,
        "create_user_data_backup",
        lambda **kwargs: calls.append(kwargs) or Summary(),
    )
    monkeypatch.setattr(desktop_app, "_message_box_info", lambda *_args: None)

    result = desktop_app._run_utility_mode(
        [
            "--backup-local-data",
            "--backup-output",
            str(tmp_path / "backup.zip"),
            "--include-private-config",
        ]
    )

    assert result == 0
    assert calls == [
        {
            "output_path": tmp_path / "backup.zip",
            "include_private_config": True,
        }
    ]
