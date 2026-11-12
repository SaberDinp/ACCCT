"""Exercise desktop process ownership, persistent storage, and helper dispatch."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from desktop.backend import dispatch_module
from telemetry import runtime


ROOT = Path(__file__).resolve().parents[1]


def test_helper_commands_reenter_frozen_backend(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime.module_command("yt_dlp") == [sys.executable, "--run-module", "yt_dlp"]
    assert runtime.module_command("streamlink_cli") == [sys.executable, "--run-module", "streamlink"]
    with pytest.raises(ValueError):
        runtime.module_command("arbitrary_module")
    with pytest.raises(ValueError):
        dispatch_module(["--run-module", "os"])
    monkeypatch.setattr(sys, "frozen", False)
    assert runtime.module_command("yt_dlp") == [sys.executable, "-m", "yt_dlp"]


def test_desktop_javascript_runtime_preserves_spaces(monkeypatch):
    monkeypatch.setenv("APEX_JS_RUNTIME", "node:/Applications/Previous app/runtime")
    monkeypatch.setenv("ACCC_JS_RUNTIME", "node:/Applications/ACCC Telemetry.app/Contents/MacOS/ACCC Telemetry")
    assert runtime.javascript_runtime_args() == [
        "--js-runtimes", "node:/Applications/ACCC Telemetry.app/Contents/MacOS/ACCC Telemetry"]
    monkeypatch.delenv("ACCC_JS_RUNTIME")
    assert runtime.javascript_runtime_args() == ["--js-runtimes", "node:/Applications/Previous app/runtime"]


@pytest.mark.parametrize("stop", ["shutdown", "eof"])
@pytest.mark.parametrize("occupied", [False, True])
def test_backend_readiness_storage_and_graceful_exit(tmp_path, stop, occupied):
    data_root = tmp_path / "Application Support" / "ACCC Telemetry"
    with (tmp_path / "backend.log").open("w+") as log, socket.socket() as foreign:
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        requested_port = foreign.getsockname()[1] if occupied else 0
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "desktop/backend.py"), "--data-root", str(data_root),
             "--port", str(requested_port)],
            cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            ready = json.loads(lines.get(timeout=25))
            assert ready["type"] == "ready" and ready["pid"] == process.pid
            assert 0 < ready["port"] <= 65535
            assert ready["port"] != requested_port
            base = f"http://127.0.0.1:{ready['port']}"

            def request(path, payload=None):
                req = Request(base + path,
                              data=json.dumps(payload).encode() if payload is not None else None,
                              headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=10) as response:
                    return response.read()

            assert b"ACCC" in request("/")
            assert b"function" in request("/static/app.js")
            assert json.loads(request("/api/catalog"))
            assert json.loads(request("/api/validation"))
            status = json.loads(request("/api/status"))
            assert ready["host"] == "127.0.0.1"
            assert "urls" not in status
            assert status["state"] == "idle"
            # The occupied listener never receives a health check or user data.
            foreign.settimeout(0.05)
            with pytest.raises(TimeoutError):
                foreign.accept()
            with pytest.raises(HTTPError) as error:
                request("/api/source", {"kind": "browser"})
            assert error.value.code in {400, 422}
            for removed in ("/phone", "/api/network", "/api/qr"):
                with pytest.raises(HTTPError) as missing:
                    request(removed)
                assert missing.value.code == 404
            assert (data_root / "data/telemetry.sqlite3").is_file()
            assert not (tmp_path / "data").exists()
            if stop == "shutdown":
                process.stdin.write("shutdown\n")
                process.stdin.flush()
            else:
                process.stdin.close()
            assert process.wait(timeout=30) == 0
            with sqlite3.connect(data_root / "data/telemetry.sqlite3") as db:
                assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            log.seek(0)
            assert "Application shutdown complete" in log.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()


@pytest.mark.parametrize("module", ["yt_dlp", "streamlink"])
def test_helper_dispatch_executes_packaged_cli_entry_points(module):
    result = subprocess.run(
        [sys.executable, str(ROOT / "desktop/backend.py"), "--run-module", module, "--version"],
        text=True, capture_output=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert any(character.isdigit() for character in result.stdout)


def test_restart_keeps_saved_port_after_http_shutdown(tmp_path):
    port = 0
    for attempt in range(2):
        with (tmp_path / f"restart-{attempt}.log").open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "desktop/backend.py"), "--data-root", str(tmp_path / "library"),
                 "--host", "127.0.0.1", "--port", str(port)],
                cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
            records = queue.Queue()
            threading.Thread(target=lambda: records.put(process.stdout.readline()), daemon=True).start()
            try:
                ready = json.loads(records.get(timeout=25))
                # Windows retains exclusive sockets through TIME_WAIT; its
                # safe fallback may select another free port after restart.
                if attempt and sys.platform != "win32":
                    assert ready["port"] == port
                port = ready["port"]
                origin = f"http://127.0.0.1:{port}"
                with urlopen(origin + "/api/status", timeout=10) as response:
                    assert json.load(response)["state"] == "idle"
                # Stop after HTTP use; the server must release its local port.
                process.stdin.write("shutdown\n")
                process.stdin.flush()
                assert process.wait(timeout=30) == 0
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()


def test_desktop_backend_rejects_network_binding(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "desktop/backend.py"),
                             "--data-root", str(tmp_path), "--host", "0.0.0.0"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
