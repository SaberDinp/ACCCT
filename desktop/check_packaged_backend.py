#!/usr/bin/env python3
"""Exercise a built backend with fresh data, without the source checkout.

macOS: python desktop/check_packaged_backend.py desktop/build/backend-macos-arm64/accc-backend/accc-backend
Windows: python desktop/check_packaged_backend.py path/to/python.exe path/to/app/desktop/backend.py
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Provide the backend executable, followed by its optional entry script.")
    command = [str(Path(value).resolve()) for value in sys.argv[1:]]
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__", "ACCC_JS_RUNTIME", "APEX_JS_RUNTIME"):
        environment.pop(name, None)
    reports = {}
    with tempfile.TemporaryDirectory(prefix="accc packaged backend ") as temporary:
        cwd = Path(temporary)
        root = cwd / "Application Data"
        saved_port = 0
        for module in ("yt_dlp", "streamlink"):
            result = subprocess.run(command + ["--run-module", module, "--version"],
                                    cwd=cwd, env=environment, text=True,
                                    capture_output=True, timeout=60, check=True)
            reports[module] = result.stdout.strip()

        for attempt in range(2):
            with (cwd / f"stderr-{attempt}.log").open("w+") as stderr:
                process = subprocess.Popen(command + ["--data-root", str(root), "--port", str(saved_port)],
                                           cwd=cwd, env=environment, text=True,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
                records = queue.Queue()
                threading.Thread(target=lambda: records.put(process.stdout.readline()), daemon=True).start()
                try:
                    line = records.get(timeout=120)
                    if not line:
                        stderr.seek(0)
                        raise AssertionError(f"Backend exited before ready: {stderr.read()}")
                    ready = json.loads(line)
                    assert ready["type"] == "ready" and ready["pid"] == process.pid
                    assert ready["host"] == "127.0.0.1"
                    if saved_port and sys.platform != "win32":
                        assert ready["port"] == saved_port, "Restart must preserve the UI's local-storage origin"
                    saved_port = ready["port"]
                    origin = f"http://127.0.0.1:{ready['port']}"

                    def request(path, value=None, method=None):
                        headers = {"Origin": origin}
                        body = None
                        if value is not None:
                            body = json.dumps(value).encode()
                            headers["Content-Type"] = "application/json"
                        try:
                            with urlopen(Request(origin + path, data=body, headers=headers, method=method), timeout=15) as response:
                                content = response.read()
                                assert response.status == 200
                                return json.loads(content) if "application/json" in response.headers.get("content-type", "") else content
                        except HTTPError as error:
                            detail = error.read().decode("utf-8", errors="replace")
                            raise AssertionError(f"{method or 'GET'} {path}: HTTP {error.code}: {detail}") from error

                    assert b"ACCC Telemetry" in request("/")
                    assert b"--accent" in request("/static/styles.css")
                    if attempt == 0:
                        # Compare the bundled UI to this release checkout on every
                        # native target, catching stale platform-specific resources.
                        web = Path(__file__).resolve().parents[1] / "web"
                        hashes = {}
                        for source in sorted(web.rglob("*")):
                            if not source.is_file():
                                continue
                            relative = source.relative_to(web).as_posix()
                            expected = hashlib.sha256(source.read_bytes()).hexdigest()
                            actual = hashlib.sha256(request("/static/" + relative)).hexdigest()
                            assert actual == expected, f"Stale packaged UI: {relative}"
                            hashes[relative] = actual
                        reports["shared_ui"] = {"files_verified": len(hashes), "sha256": hashes}
                    assert request("/api/status")["state"] == "idle"
                    assert request("/api/catalog")
                    tracks = request("/api/tracks")
                    assert tracks
                    assert request("/api/validation")
                    profile = request("/api/profile")
                    if attempt == 0:
                        assert request("/api/sessions") == []
                        profile["name"] = "Packaged persistence check"
                        request("/api/profile", profile, "PUT")
                        # Make a real video in the build environment, then let
                        # the packaged decoder and HUD extractor process it.
                        import av

                        video_path = cwd / "test recording.mp4"
                        with av.open(str(video_path), "w") as output:
                            stream = output.add_stream("mpeg4", rate=30)
                            stream.width, stream.height = 320, 180
                            stream.pix_fmt = "yuv420p"
                            for index in range(30):
                                frame = av.VideoFrame(320, 180, "yuv420p")
                                for plane in frame.planes:
                                    plane.update(bytes([128]) * plane.buffer_size)
                                frame.pts = index
                                for packet in stream.encode(frame):
                                    output.mux(packet)
                            for packet in stream.encode():
                                output.mux(packet)
                        media = video_path.read_bytes()
                        boundary = "accc-packaged-smoke-upload"
                        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.mp4"\r\n'
                                'Content-Type: video/mp4\r\n\r\n').encode() + media + f"\r\n--{boundary}--\r\n".encode()
                        upload = Request(origin + "/api/upload", data=body,
                                         headers={"Origin": origin, "Content-Type": f"multipart/form-data; boundary={boundary}"})
                        with urlopen(upload, timeout=15) as response:
                            uploaded = json.load(response)
                        source = request("/api/source", {"kind": "file", "path": uploaded["path"], "realtime": False}, "POST")
                        deadline = time.monotonic() + 30
                        while time.monotonic() < deadline:
                            status = request("/api/status")
                            if status["state"] in {"ended", "error"}:
                                break
                            time.sleep(0.1)
                        assert status["state"] == "ended" and status["error"] is None, status
                        assert request(f"/api/sessions/{source['session_id']}/video/media") == media
                        assert len(request("/api/sessions")) == 1
                        reports["video"] = "upload, decode, analysis and playback passed"
                    else:
                        assert profile["name"] == "Packaged persistence check"
                        assert len(request("/api/sessions")) == 1
                    reports[f"launch_{attempt + 1}"] = {"port": ready["port"], "resources": "ok", "persistence": "ok"}
                    process.stdin.write("shutdown\n")
                    process.stdin.flush()
                    assert process.wait(timeout=30) == 0
                except Exception:
                    stderr.flush()
                    stderr.seek(0)
                    print(f"Backend stderr (launch {attempt + 1}):\n{stderr.read()}", file=sys.stderr)
                    raise
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    process.stdin.close()
                    process.stdout.close()
        assert (root / "data" / "telemetry.sqlite3").is_file()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
