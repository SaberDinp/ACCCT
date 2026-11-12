"""Read-only lap-boundary refinement and bounded visual search jobs."""
from __future__ import annotations

import copy
import threading
import time
import uuid

from telemetry.analysis import SessionAnalyzer


def timer_candidates(samples: list[dict]) -> dict:
    analyzer = SessionAnalyzer(retain_samples=False)
    previous_segment = None
    for stored in samples:
        sample = dict(stored)
        segment = sample.get("segment_id")
        if previous_segment is not None and segment is not None and segment != previous_segment:
            analyzer.discontinuity("recorded_segment_change")
        if segment is not None:
            previous_segment = segment
        if "raw_lap_time_seconds" in sample:
            sample["lap_time_seconds"] = sample["raw_lap_time_seconds"]
            sample["lap_time"] = sample.get("raw_lap_time")
        for key in ("timer_pending", "timer_outlier"):
            sample.pop(key, None)
        if not sample.get("active", True):
            analyzer.discontinuity(sample.get("gap_reason") or "recorded_gap")
            sample["hud_visible"] = False
        analyzer.process(sample)
    candidates = []
    for lap in analyzer.laps:
        if not lap["complete"] or lap["duration"] < 20:
            continue
        start = samples[lap["start_sample"]]
        end = samples[lap["end_sample"]]
        if start.get("segment_id") is None or start.get("segment_id") != end.get("segment_id"):
            continue
        candidates.append({"id": f"timer-{lap['id']}", "lap_id": lap["id"],
                           "start_time": lap["start_time"], "end_time": lap["end_time"],
                           "duration": lap["duration"], "segment_id": start["segment_id"],
                           "complete": True, "confirmed": False, "valid": lap.get("valid"),
                           "method": "timer_backprojection", "end_observed_at": lap.get("end_observed_at")})
    warnings = []
    if not candidates:
        warnings.append("No complete lap has two reliable clock boundaries. A calibrated visual crossing search is available.")
    if any(not lap["complete"] for lap in analyzer.laps):
        warnings.append("Interrupted or partial clock laps were excluded.")
    return {"candidates": candidates, "warnings": warnings, "method": "recorded_clock_reanalysis"}


class LapDetectionJobs:
    """One independent visual worker; never changes captures or stored samples."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._closed = False

    def start(self, path: str, session_id: str, settings: dict,
              target_path: str | None = None, target_session_id: str | None = None) -> dict:
        with self._lock:
            if self._closed:
                raise ValueError("The lap detector is shutting down.")
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("A visual crossing search is already running.")
            self._cancel.clear()
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "session_id": session_id, "state": "running", "result": None,
                   "error": None, "started_at": time.time(), "kind": "landmark" if target_path else "lap",
                   "target_session_id": target_session_id}
            self._jobs[job_id] = job
            while len(self._jobs) > 20:
                del self._jobs[next(iter(self._jobs))]
            self._thread = threading.Thread(target=self._run, args=(job_id, path, copy.deepcopy(settings), target_path), daemon=True,
                                            name="visual-lap-search")
            self._thread.start()
            return copy.deepcopy(job)

    def _run(self, job_id: str, path: str, settings: dict, target_path: str | None = None) -> None:
        try:
            from telemetry.visual_laps import find_visual_laps
            def progress(update):
                with self._lock:
                    self._jobs[job_id].update(update)
            if target_path:
                from telemetry.visual_laps import match_visual_landmark
                result = match_visual_landmark(path, target_path=target_path, **settings,
                                               cancel_event=self._cancel, progress=progress)
            else:
                result = find_visual_laps(path, **settings, cancel_event=self._cancel, progress=progress)
            with self._lock:
                self._jobs[job_id].update(state="cancelled" if self._cancel.is_set() else "completed", result=result)
        except Exception as error:
            with self._lock:
                self._jobs[job_id].update(state="cancelled" if self._cancel.is_set() else "failed", error=str(error))

    def get(self, job_id: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._jobs[job_id])

    def active_session_ids(self) -> set[str]:
        with self._lock:
            return {session_id for job in self._jobs.values() if job["state"] == "running"
                    for session_id in (job["session_id"], job.get("target_session_id")) if session_id}

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs[job_id]
            if job["state"] == "running":
                self._cancel.set()
            return copy.deepcopy(job)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._cancel.set()
            worker = self._thread
        if worker is not None:
            worker.join(timeout=10)
