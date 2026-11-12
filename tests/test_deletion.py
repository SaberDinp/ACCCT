"""Deletion safety with isolated recordings, media, and worker state."""
from pathlib import Path
import threading

import pytest

from telemetry.engine import TelemetryEngine
from telemetry.imports import ReferenceImports
from telemetry.storage import SessionStore
from telemetry.vision import HUDExtractor
from tests.test_engine import make_video, observed_frame, wait_until


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(HUDExtractor, "extract", observed_frame)
    root = tmp_path / "project"
    root.mkdir()
    engine = TelemetryEngine(root)
    yield engine
    engine.close()


def recording(engine, path, status="completed"):
    identifier = engine.store.create_session({"kind": "file", "path": str(path), "name": Path(path).name})
    engine.store.add_sample(identifier, {"time": 0, "active": True, "speed": 72})
    engine.store.save_summary(identifier, {"samples": 1}, [{"type": "brake"}], [{"id": 1}], status=status)
    return identifier


def managed_video(engine, name="sample.mp4"):
    path = engine.root / "data/uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"isolated fixture")
    return path


def test_store_deletion_removes_children_and_pending_samples_only_for_target(tmp_path):
    database = tmp_path / "library.sqlite3"
    with SessionStore(database) as store:
        target = store.create_session("target.mp4")
        other = store.create_session("other.mp4")
        store.add_sample(target, {"time": 0})
        store.save_summary(target, {}, [{"type": "brake"}], [{"id": 1}], status="completed")
        store.add_sample(target, {"time": 1})
        store.add_sample(other, {"time": 2})
        store.delete_session(target)
        store.flush()
        assert store.get_session(target) is None and store.get_samples(target) == []
        assert store.get_samples(other) == [{"time": 2}]
        assert [row["id"] for row in store.list_session_sources()] == [other]
        for table in ("events", "laps"):
            assert store._db.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (target,)).fetchone()[0] == 0
        with pytest.raises(KeyError):
            store.add_sample(target, {"time": 3})
        with pytest.raises(KeyError):
            store.delete_session(target)
    with SessionStore(database) as reopened:
        assert reopened.get_session(target) is None


def test_original_video_is_never_removed_and_default_keeps_media(engine):
    path = engine.root / "acc_video.mp4"
    path.write_bytes(b"original")
    identifier = recording(engine, path)
    preview = engine.deletion_preview(identifier)
    assert preview["can_delete"] and not preview["video"]["can_delete"]
    assert not preview["video"]["managed"]
    with pytest.raises(ValueError, match="app-managed"):
        engine.delete_session(identifier, delete_video=True)
    assert engine.store.get_session(identifier) is not None
    assert engine.delete_session(identifier)["video_retained"]
    assert path.read_bytes() == b"original"


def test_unshared_managed_media_can_be_explicitly_removed(engine):
    path = engine.root / "data/references/download/media.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"download")
    metadata = path.with_name("media.info.json")
    metadata.write_text('{}')
    other = managed_video(engine, "other.mp4")
    identifier = recording(engine, path)
    other_id = recording(engine, other)
    assert engine.deletion_preview(identifier)["video"]["can_delete"]
    response = engine.delete_session(identifier, delete_video=True)
    assert response == {"session_id": identifier, "deleted": True, "video_deleted": True,
                        "video_retained": False, "warnings": []}
    assert not path.exists() and metadata.exists() and other.exists()
    assert engine.store.get_session(other_id) is not None


def test_shared_media_and_path_aliases_are_retained(engine):
    path = managed_video(engine)
    first = recording(engine, path)
    second = recording(engine, Path("data/uploads") / path.name)
    preview = engine.deletion_preview(first)
    assert preview["video"]["shared_session_ids"] == [second]
    assert not preview["video"]["can_delete"]
    with pytest.raises(ValueError, match="share"):
        engine.delete_session(first, delete_video=True)
    engine.delete_session(first)
    assert path.exists()
    assert engine.deletion_preview(second)["video"]["can_delete"]


@pytest.mark.parametrize("link_directory", [False, True])
def test_symlink_escape_and_outside_media_cannot_be_removed(engine, link_directory):
    outside = engine.root.parent / "outside.mp4"
    outside.write_bytes(b"keep outside")
    uploads = engine.root / "data/uploads"
    if link_directory:
        uploads.symlink_to(engine.root.parent, target_is_directory=True)
        path = uploads / outside.name
    else:
        uploads.mkdir()
        path = uploads / "linked.mp4"
        path.symlink_to(outside)
    identifier = recording(engine, path)
    assert not engine.deletion_preview(identifier)["video"]["can_delete"]
    with pytest.raises(ValueError, match="app-managed"):
        engine.delete_session(identifier, delete_video=True)
    engine.delete_session(identifier)
    assert outside.read_bytes() == b"keep outside"


def test_media_unlink_error_is_reported_without_claiming_success(engine, monkeypatch):
    path = managed_video(engine)
    identifier = recording(engine, path)
    monkeypatch.setattr(engine, "_unlink_managed_video", lambda path: (_ for _ in ()).throw(PermissionError("fixture denied")))
    result = engine.delete_session(identifier, delete_video=True)
    assert result["deleted"] and not result["video_deleted"] and result["video_retained"]
    assert "fixture denied" in result["warnings"][0]
    assert engine.store.get_session(identifier) is None and path.exists()


def test_active_recordings_and_shared_active_source_are_blocked(engine):
    path = managed_video(engine)
    identifier = recording(engine, path, status="recording")
    assert not engine.deletion_preview(identifier)["can_delete"]
    with pytest.raises(ValueError, match="still"):
        engine.delete_session(identifier)
    engine.store.save_summary(identifier, {}, status="completed")
    engine.source, engine.session_id, engine.state = {"kind": "file", "path": str(path)}, "another", "paused"
    assert not engine.deletion_preview(identifier)["can_delete"]
    engine.source = engine.session_id = None
    engine.state = "idle"


def test_ended_current_session_detaches_without_later_resurrection(engine):
    path = managed_video(engine)
    make_video(path)
    engine.start({"kind": "file", "path": str(path), "realtime": False})
    wait_until(lambda: engine.state == "ended" and not engine.worker.is_alive())
    identifier = engine.session_id
    engine.delete_session(identifier)
    assert engine.status()["session_id"] is None and engine.state == "idle"
    assert not engine.history and engine.latest is None and engine.preview is None
    engine.stop()
    assert engine.store.get_session(identifier) is None


def test_import_in_use_detection_and_terminal_link_invalidation(engine):
    importer = ReferenceImports(engine)
    path = managed_video(engine)
    identifier = recording(engine, path)
    other = recording(engine, path, status="recording")
    importer.jobs["job"] = {"id": "job", "state": "analyzing", "session_id": other,
                            "source": {"kind": "youtube", "url": "https://youtu.be/fixture"}}
    assert importer.deletion_block_reason(identifier, str(path))
    assert importer.deletion_block_reason(other)
    with pytest.raises(ValueError, match="active import"):
        importer.forget_session(other)
    importer.jobs["job"]["state"] = "completed"
    assert importer.deletion_block_reason(identifier, str(path)) is None
    importer.forget_session(other)
    assert importer.jobs["job"]["session_id"] is None and importer.jobs["job"]["recording_deleted"]
    importer.close()


def test_source_start_waits_for_lifecycle_before_validating_removed_file(engine):
    path = managed_video(engine)
    started = threading.Event()
    errors = []

    def start():
        started.set()
        try:
            engine.start({"kind": "file", "path": str(path)})
        except ValueError as error:
            errors.append(str(error))

    with engine.lifecycle_lock:
        worker = threading.Thread(target=start)
        worker.start()
        assert started.wait(1)
        path.unlink()
    worker.join(timeout=2)
    assert not worker.is_alive() and errors == ["Video file does not exist."]
    assert engine.store.list_sessions() == []
