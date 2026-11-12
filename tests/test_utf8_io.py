"""Published UTF-8 assets must not depend on the host's Windows ANSI code page."""
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from telemetry.engine import TelemetryEngine
from telemetry.imports import ReferenceImports
from telemetry.profiles import default_profile
from telemetry import tracks


@pytest.fixture
def windows_ansi_defaults(monkeypatch):
    """Exercise the Windows CP1252 default even on a UTF-8 build host."""
    read_text, write_text = Path.read_text, Path.write_text

    def read(path, encoding=None, errors=None, newline=None):
        return read_text(path, encoding=encoding or "cp1252", errors=errors, newline=newline)

    def write(path, data, encoding=None, errors=None, newline=None):
        return write_text(path, data, encoding=encoding or "cp1252", errors=errors, newline=newline)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "write_text", write)


def test_track_ranges_remain_readable_with_windows_ansi_defaults(windows_ansi_defaults, tmp_path):
    probe = tmp_path / "encoding-probe.txt"
    probe.write_bytes("3–4".encode("utf-8"))
    assert probe.read_text() != "3–4"  # The regression simulation is active.
    tracks._load_track.cache_clear()
    try:
        loaded = {track["id"]: tracks.get_track(track["id"]) for track in tracks.list_tracks()}
        assert len(loaded) == 25
        for track_id, corner_name, expected in (
            ("spa", "Raidillon", "3–4"),
            ("kyalami", "Jukskei Sweep", "3–4"),
            ("misano", "Variante del Parco", "1–3"),
        ):
            corner = next(corner for corner in loaded[track_id]["corners"] if corner["name"] == corner_name)
            assert corner["number"] == expected
    finally:
        tracks._load_track.cache_clear()


def test_unicode_profile_survives_loading_and_saving(windows_ansi_defaults, tmp_path):
    profile = default_profile()
    profile["name"] = "東京 – Driver’s HUD 🏁"
    folder = tmp_path / "data"
    folder.mkdir()
    path = folder / "profile.json"
    path.write_bytes(json.dumps(profile, ensure_ascii=False).encode("utf-8"))
    engine = TelemetryEngine(tmp_path)
    try:
        assert engine.profile["name"] == profile["name"]
        updated = engine.set_profile({**engine.profile, "name": "鈴鹿 – Reference 🏁"})
        assert json.loads(path.read_bytes())["name"] == updated["name"]
    finally:
        engine.close()
    reopened = TelemetryEngine(tmp_path)
    try:
        assert reopened.profile["name"] == "鈴鹿 – Reference 🏁"
    finally:
        reopened.close()


def test_recorded_video_title_uses_downloader_utf8_metadata(windows_ansi_defaults, tmp_path, monkeypatch):
    title = "東京 – Driver’s reference 🏁"

    def finished_download(command, **_):
        template = Path(command[command.index("-o") + 1])
        template.with_name("media.mp4").write_bytes(b"fixture video")
        template.with_name("media.info.json").write_bytes(json.dumps({"title": title}, ensure_ascii=False).encode("utf-8"))
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    monkeypatch.setattr("telemetry.imports.subprocess.Popen", finished_download)
    engine = TelemetryEngine(tmp_path)
    importer = ReferenceImports(engine)
    try:
        path, actual = importer._download_youtube("utf8-metadata", {"url": "https://youtu.be/c0SJgXUahEM"}, threading.Event())
        assert path.name == "media.mp4"
        assert actual == title
    finally:
        importer.close()
        engine.close()


def test_release_text_is_utf8_on_windows_too(windows_ansi_defaults, tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / ".github/scripts/prepare_release.py"
    spec = importlib.util.spec_from_file_location("utf8_release_fixture", script)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.delenv("RELEASE_TAG", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    (tmp_path / "desktop").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "desktop/package.json").write_bytes(b'{"version":"9.8.7"}')
    install = "ACCC Telemetry {version}\n東京 → Rice Racing 🏁\n"
    notes = "# 9.8.7\nDrivers’ laps – 東京 🏁\n"
    (tmp_path / "docs/INSTALL.txt").write_bytes(install.encode("utf-8"))
    (tmp_path / "docs/RELEASE_NOTES.md").write_bytes(notes.encode("utf-8"))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for suffix in release.SUFFIXES:
        with (artifacts / f"ACCC-Telemetry-9.8.7-{suffix}").open("wb") as stream:
            stream.seek(1_000_000)
            stream.write(b"0")
    release.prepare(artifacts)
    assert (artifacts / "INSTALL.txt").read_bytes().decode("utf-8").replace("\r\n", "\n") == install.replace("{version}", "9.8.7")
    assert (tmp_path / "release-notes.md").read_bytes().decode("utf-8").replace("\r\n", "\n") == notes
