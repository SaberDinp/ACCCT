import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from telemetry.storage import SessionStore


def test_session_samples_events_and_laps_survive_reopen(tmp_path):
    path = tmp_path / "nested" / "sessions.sqlite3"
    with SessionStore(path) as store:
        session_id = store.create_session({"kind": "file", "name": "test.mp4"}, {"track": "Spa"})
        store.add_sample(session_id, {"time": 0.2, "active": True, "segment_id": 1, "throttle": 90})
        store.add_sample(session_id, {"time": 0.1, "active": False, "segment_id": None, "throttle": None})
        store.save_summary(session_id, {"active_seconds": 0.1}, [{"type": "gap"}], [{"id": 1}], status="completed")
    with SessionStore(path) as store:
        session = store.get_session(session_id)
        assert session["status"] == "completed"
        assert session["sample_count"] == 2
        assert session["source"]["name"] == "test.mp4"
        assert session["metadata"]["track"] == "Spa"
        assert session["events"] == [{"type": "gap"}]
        assert session["laps"] == [{"id": 1}]
        assert [sample["time"] for sample in store.get_samples(session_id)] == [0.2, 0.1]
        assert store.get_samples(session_id, limit=1)[0]["time"] == 0.1
        assert store.list_sessions()[0]["id"] == session_id


def test_read_flushes_pending_samples_and_summary_replacement(tmp_path):
    with SessionStore(tmp_path / "session.sqlite3", batch_size=100) as store:
        session_id = store.create_session("video")
        store.add_sample(session_id, {"time": 0, "brake": None})
        assert store.get_session(session_id)["sample_count"] == 1
        store.save_summary(session_id, {}, [{"type": "brake"}], [{"id": 1}])
        store.save_summary(session_id, {}, [], [])
        session = store.get_session(session_id)
        assert session["events"] == [] and session["laps"] == []
        assert json.loads(json.dumps(store.get_samples(session_id)))[0]["brake"] is None


def test_missing_sessions_and_invalid_json(tmp_path):
    with SessionStore(tmp_path / "session.sqlite3") as store:
        assert store.get_session("absent") is None
        assert store.get_samples("absent") == []
        with pytest.raises(KeyError):
            store.add_sample("absent", {"time": 0})
        with pytest.raises(KeyError):
            store.save_summary("absent", {})
        session_id = store.create_session("video")
        with pytest.raises(ValueError):
            store.add_sample(session_id, {"time": 0, "speed": float("nan")})


def test_concurrent_capture_and_reads_do_not_drop_samples(tmp_path):
    with SessionStore(tmp_path / "session.sqlite3", batch_size=7) as store:
        session_id = store.create_session("video")

        def write_chunk(start):
            for number in range(start, start + 50):
                store.add_sample(session_id, {"time": number})
                store.list_sessions()

        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(write_chunk, (0, 50, 100)))
        assert len(store.get_samples(session_id)) == 150
        assert store.get_session(session_id)["sample_count"] == 150


def test_restart_marks_unfinished_sessions_interrupted_and_preserves_data(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with SessionStore(path) as store:
        unfinished = store.create_session("unfinished.mp4")
        completed = store.create_session("completed.mp4")
        store.add_sample(unfinished, {"time": 0.1, "speed": 70})
        store.save_summary(completed, {}, status="completed")
    with SessionStore(path) as restarted:
        assert restarted.get_session(unfinished)["status"] == "interrupted"
        assert restarted.get_samples(unfinished)[0]["speed"] == 70
        assert restarted.get_session(completed)["status"] == "completed"


def test_metadata_updates_preserve_source_settings_and_replace_profile_atomically(tmp_path):
    with SessionStore(tmp_path / "metadata.sqlite3") as store:
        session_id = store.create_session({"kind": "file"}, {"role": "reference", "profile": {"name": "initial"}})
        updated = store.update_metadata(session_id, {"profile": {"name": "resolved"}, "calibration": "manual"})
        assert updated == {"role": "reference", "profile": {"name": "resolved"}, "calibration": "manual"}
        updated["profile"]["name"] = "mutated"
        assert store.get_session(session_id)["metadata"]["profile"]["name"] == "resolved"
        with pytest.raises(ValueError):
            store.update_metadata(session_id, {"invalid": float("nan")})
        assert "invalid" not in store.get_session(session_id)["metadata"]
        with pytest.raises(KeyError):
            store.update_metadata("missing", {})
