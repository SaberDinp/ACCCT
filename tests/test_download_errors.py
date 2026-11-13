"""Anonymous download diagnostics use mocked processes, never online media."""
import io
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from telemetry import download_errors, imports, review_audio


@pytest.mark.parametrize("log, expected", [
    ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot.", "bot verification"),
    ("ERROR: [youtube] abc: This video is private. Sign in if you've been granted access.", "requires account access"),
    ("ERROR: [youtube] abc: Sign in to confirm your age.", "requires account access"),
    ("ERROR: [youtube] abc: This members-only content is not available.", "requires account access"),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed.", "unavailable, removed, or restricted"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country.", "restricted in your region"),
    ("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests", "rate-limiting downloads"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "refused the download request"),
    ("ERROR: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "secure connection"),
    ("ERROR: Unable to download webpage: connection timed out", "connection to YouTube failed or timed out"),
    ("ERROR: [youtube] abc: getaddrinfo failed", "connection to YouTube failed or timed out"),
    ("ERROR: No usable JavaScript runtime found", "JavaScript challenge solver failed"),
    ("ERROR: [jsc:node] No usable challenge solver core script available", "JavaScript challenge solver failed"),
    ("ERROR: Requested format is not available. Use --list-formats", "supported video or audio format"),
    ("ERROR: unable to write data: [Errno 28] No space left on device", "could not be saved"),
    ("ERROR: unable to open for writing: [WinError 5] Access is denied", "could not be saved"),
    ("ERROR: Unexpected provider response", "downloader could not finish"),
])
def test_failure_message_identifies_the_observed_reason(tmp_path, log, expected):
    path = tmp_path / "download.log"
    path.write_text(log, encoding="utf-8")
    message = download_errors.youtube_download_error(path)
    assert message.startswith("YouTube download failed.")
    assert expected in message
    assert str(path.resolve()) in message
    assert "ERROR:" not in message


@pytest.mark.parametrize("log, expected, absent", [
    ("WARNING: Sign in to access extra formats\nERROR: HTTP Error 403: Forbidden",
     "refused the download request", "requires account access"),
    ("WARNING: No supported JavaScript runtime\nERROR: [youtube] Private video",
     "requires account access", "JavaScript challenge solver failed"),
    ("WARNING: Sign in to access extra formats\nERROR: unrecognized failure",
     "downloader could not finish", "requires account access"),
    ("WARNING: No supported JavaScript runtime\nERROR: Requested format is not available",
     "JavaScript challenge solver failed", "supported video or audio format"),
    ("ERROR: Sign in to confirm you're not a bot\nERROR: [Errno 28] No space left on device",
     "could not be saved", "bot verification"),
])
def test_fatal_error_takes_precedence_over_warnings_and_earlier_errors(tmp_path, log, expected, absent):
    path = tmp_path / "download.log"
    path.write_text(log)
    message = download_errors.youtube_download_error(path)
    assert expected in message
    assert absent not in message


def test_log_read_is_bounded_and_raw_urls_or_secrets_are_never_echoed(tmp_path, monkeypatch):
    path = tmp_path / "download.log"
    payload = (b"ERROR: Sign in to confirm you're not a bot\n" + b"x" * (download_errors.LOG_TAIL_BYTES * 2)
               + b"\nERROR: HTTP Error 429: https://example.test/video?token=private-secret\n")
    sizes = []

    class BoundedLog(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            assert 0 < size <= download_errors.LOG_TAIL_BYTES
            return super().read(size)

    original = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs:
                        BoundedLog(payload) if self == path else original(self, *args, **kwargs))
    message = download_errors.youtube_download_error(path)
    assert sizes == [download_errors.LOG_TAIL_BYTES]
    assert "rate-limiting downloads" in message
    assert "bot verification" not in message
    assert "private-secret" not in message and "example.test" not in message


def test_missing_or_non_utf8_logs_produce_safe_diagnostics(tmp_path):
    path = tmp_path / "download.log"
    assert "downloader could not finish" in download_errors.youtube_download_error(path)
    path.write_bytes(b"\xff\xfe\n\x1b[31mERROR:\x1b[0m HTTP Error 403: Forbidden\n")
    assert "refused the download request" in download_errors.youtube_download_error(path)


@pytest.mark.parametrize("log, eligible", [
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", True),
    ("WARNING: earlier media issue\nERROR: unable to download video data: HTTP Error 403: Forbidden", True),
    ("ERROR: unable to download webpage: HTTP Error 403: Forbidden", False),
    ("WARNING: unable to download video data: HTTP Error 403: Forbidden", False),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden\nERROR: Private video", False),
    ("ERROR: unable to download video data: HTTP Error 403: Sign in to confirm you're not a bot", False),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", False),
])
def test_only_fatal_media_403_is_eligible_for_a_media_fallback(tmp_path, log, eligible):
    path = tmp_path / "download.log"
    path.write_text(log)
    assert download_errors.youtube_media_forbidden(path) is eligible
    if eligible:
        assert download_errors.youtube_download_failure_kind(path) == "forbidden"


def failed_process(monkeypatch, module, diagnostic, commands):
    class FailedDownload:
        returncode = 1

        def __init__(self, command, **kwargs):
            commands.append(command)
            kwargs["stdout"].write(diagnostic.encode())

        def poll(self):
            return self.returncode

    monkeypatch.setattr(module.subprocess, "Popen", FailedDownload)


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_download_failures_retain_diagnostics_and_remain_anonymous(tmp_path, monkeypatch, kind):
    module = imports if kind == "video" else review_audio
    commands = []
    failed_process(monkeypatch, module, "WARNING: missing extra formats\nERROR: HTTP Error 403: Forbidden", commands)
    monkeypatch.setattr(module, "javascript_runtime_args", lambda: ["--js-runtimes", "node:/test/runtime"])
    engine = SimpleNamespace(root=tmp_path, store=None)
    url = "https://www.youtube.com/watch?v=RJl2an-kbZk"
    with pytest.raises(download_errors.YouTubeDownloadError) as error:
        if kind == "video":
            importer = imports.ReferenceImports(engine)
            importer._download_youtube("job", {"url": url}, threading.Event())
            pytest.fail("Failed video download returned media")
        else:
            folder = tmp_path / "audio"
            folder.mkdir()
            audio = review_audio.ReviewAudio(engine)
            audio._download(url, folder, threading.Event(), time.monotonic())
            pytest.fail("Failed audio download returned media")
    command = commands[0]
    assert "refused the download request" in str(error.value)
    assert "--no-warnings" not in command and "--ignore-config" in command
    assert not set(command) & {"--cookies", "--cookies-from-browser", "--username", "--password", "--netrc"}
    assert command[-2:] == ["--", url]
    log = tmp_path / ("data/references/job/download.log" if kind == "video" else "audio/download.log")
    assert "WARNING:" in log.read_text()
    assert str(log.resolve()) in str(error.value)


def test_import_worker_keeps_complete_log_path_after_failure(tmp_path, monkeypatch):
    from main import create_app
    from fastapi.testclient import TestClient

    # Reproduce an installed data directory long enough to exceed the old
    # 400-character UI truncation, even with Linux's shorter pytest temp root.
    # Pad only as needed so ordinary Windows paths remain below MAX_PATH.
    root = tmp_path / "Installed Application Data"
    root = root.with_name(root.name + "-" * max(0, 125 - len(str(root))))
    root.mkdir()
    commands = []
    failed_process(monkeypatch, imports, "ERROR: Sign in to confirm you're not a bot", commands)
    with TestClient(create_app(root=root)) as client:
        importer = client.app.state.imports
        job = importer.start({"kind": "youtube", "url": "https://youtu.be/RJl2an-kbZk"})
        importer.worker.join(timeout=3)
        result = importer.get(job["id"])
        assert result["state"] == "error"
        log = root / "data/references" / job["id"] / "download.log"
        assert len(result["error"]) > 400
        assert result["error"].endswith(str(log.resolve()))
        assert log.is_file()


def test_audio_failure_keeps_closed_log_until_recording_is_deleted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import create_app
    from tests.test_engine import make_video
    from tests.test_review_audio import prepare

    commands = []
    failed_process(monkeypatch, review_audio, "ERROR: HTTP Error 429: Too Many Requests", commands)
    with TestClient(create_app(root=tmp_path)) as client:
        engine = client.app.state.engine
        path = tmp_path / "silent.mp4"
        make_video(path)
        session_id = engine.store.create_session({"kind": "file", "path": str(path), "name": path.name,
            "original_url": "https://www.youtube.com/watch?v=RJl2an-kbZk"})
        engine.store.save_summary(session_id, {}, [], [], status="completed")
        result = prepare(client, session_id)
        client.app.state.review_audio.worker.join(timeout=3)
        directory = client.app.state.review_audio._directory(session_id)
        log = directory / "download.log"
        assert result["state"] == "error" and "rate-limiting downloads" in result["message"]
        assert result["message"].endswith(str(log.resolve()))
        assert log.is_file()
        assert list(directory.iterdir()) == [log]
        assert client.delete(f"/api/sessions/{session_id}").status_code == 200
        assert not directory.exists()
        assert path.exists()
