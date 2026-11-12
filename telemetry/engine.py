"""Video decoding and bounded, local telemetry processing."""
from __future__ import annotations

from collections import deque
import copy
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import threading
import time
from urllib.parse import urlparse

import av
import cv2
import numpy as np

from .analysis import SessionAnalyzer
from .profiles import default_profile
from .storage import SessionStore
from .vision import HUDExtractor
from .runtime import module_command, subprocess_options


class TelemetryEngine:
    def __init__(self, root: Path, database: Path | None = None):
        self.root = root
        self.data_dir = root / "data"
        self.data_dir.mkdir(exist_ok=True)
        self.store = SessionStore(database or self.data_dir / "telemetry.sqlite3")
        self.lock = threading.RLock()
        self.lifecycle_lock = threading.RLock()
        profile_path = self.data_dir / "profile.json"
        self.profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else default_profile()
        self.extractor = HUDExtractor(self.profile)
        self.profile = self.extractor.profile
        self.analyzer = SessionAnalyzer(retain_samples=False)
        self.state = "idle"
        self.source = None
        self.session_id = None
        self.latest = None
        self.history = deque(maxlen=18000)
        self.preview = None
        self.preview_time = None
        self.last_frame = None
        self.duration = None
        self.source_time = 0.0
        self.error = None
        self.processing_ms = 0.0
        self.last_received = None
        self.fps = 0.0
        self.dropped_frames = 0
        self.sequence = 0
        self.arrivals = deque(maxlen=120)
        self.worker = None
        self.reader = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.seek_to = None
        self.process = None
        self.frame_queue = queue.Queue(maxsize=1)
        self._last_preview = 0.0
        self._last_save = 0.0
        self._next_gap = None
        self._browser_last_time = None
        self._last_thumbnail = None
        self._unchanged_since = None
        self._calibration_revision = None

    def status(self):
        with self.lock:
            age = None if self.last_received is None else (time.monotonic() - self.last_received) * 1000
            stale = self.state != "running" or age is None or age > 1500
            return {
                "state": self.state, "source": self.source, "session_id": self.session_id,
                "latest": copy.deepcopy(self.latest), "summary": self.analyzer.summary(),
                "events": copy.deepcopy(self.analyzer.events[-100:]), "laps": copy.deepcopy(self.analyzer.laps),
                "processing_ms": round(self.processing_ms, 2), "fps": round(self.fps, 1),
                "source_time": self.source_time, "duration": self.duration, "error": self.error,
                "stale": stale, "sample_age_ms": round(age) if age is not None else None,
                "upstream_latency_ms": None, "dropped_frames": self.dropped_frames,
                "sequence": self.sequence,
                "calibration": copy.deepcopy(getattr(self.extractor, "calibration_status", None)),
            }

    def validate_source(self, source):
        source = dict(source)
        if source.get("context") is not None:
            from .session_context import validate_context
            source["context"] = validate_context(source["context"])
        kind = source["kind"]
        if kind == "file":
            path = Path(source.get("path") or "acc_video.mp4").expanduser()
            path = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(self.root) or path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}:
                raise ValueError("Choose a video in this project, or upload it with the file picker.")
            if not path.is_file():
                raise ValueError("Video file does not exist.")
            source.update(path=str(path), name=path.name)
        elif kind in {"twitch", "url"}:
            url = source.get("url", "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Use an http(s) stream URL without embedded credentials.")
            if kind == "twitch" and parsed.hostname.lower() not in {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}:
                raise ValueError("Enter a Twitch channel or VOD URL.")
            recorded = (kind == "twitch" and parsed.path.startswith("/videos/")) or Path(parsed.path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
            source.update(url=url, name=parsed.hostname + parsed.path, recorded=recorded)
        elif kind == "browser":
            source["name"] = "Direct browser capture"
        else:
            raise ValueError("Unsupported video source.")
        return source

    def start(self, source):
        with self.lifecycle_lock:
            source = self.validate_source(source)
            self._stop()
            with self.lock:
                self.source = source
                self.analyzer = SessionAnalyzer(retain_samples=False)
                self.extractor = HUDExtractor(self.profile)
                self.session_id = self.store.create_session(source, {"profile": self.profile, "measurement": "video-derived",
                                                                    "telemetry_context": source.get("context") or {}})
                self.history.clear()
                self.latest = self.preview = self.last_frame = None
                self.preview_time = None
                self._last_preview = 0.0
                self.error = self.duration = self.last_received = None
                self.processing_ms = self.fps = self.source_time = 0.0
                self.dropped_frames = 0
                self.arrivals.clear()
                self.stop_event = threading.Event()
                self.pause_event.clear()
                self.seek_to = None
                self._browser_last_time = None
                self._last_thumbnail = None
                self._unchanged_since = None
                self._calibration_revision = None
                self._next_gap = None
                self.frame_queue = queue.Queue(maxsize=1)
                self.state = "running" if source["kind"] == "browser" else "connecting"
                self.sequence += 1
            if source["kind"] != "browser":
                self.worker = threading.Thread(target=self._run, name="telemetry-worker", daemon=True)
                self.worker.start()
        return self.status()

    def _source_path(self, source):
        if not isinstance(source, dict) or source.get("kind") != "file" or not source.get("path"):
            return None
        path = Path(source["path"]).expanduser()
        return (self.root / path).resolve() if not path.is_absolute() else path.resolve()

    def open_video_media(self, session_id=None, expected_session_id=None):
        """Open a validated local recording before releasing lifecycle ownership.

        The returned descriptor survives a subsequent recording deletion. Each
        directory is opened without following symlinks, including a path changed
        after validation. This endpoint never proxies network sources.
        """
        with self.lifecycle_lock, self.lock:
            if session_id is None:
                if expected_session_id is not None and expected_session_id != self.session_id:
                    raise RuntimeError("The active video source changed.")
                source, identifier = self.source, self.session_id
            else:
                session = self.store.get_session(session_id)
                if session is None:
                    raise KeyError(session_id)
                source, identifier = session["source"], session_id
            if not isinstance(source, dict) or source.get("kind") != "file":
                raise ValueError("This source has no local video recording.")
            if not isinstance(source.get("path"), str) or not source["path"].strip():
                raise ValueError("This source has no local video path.")
            source = self.validate_source(source)
            root = self.root.resolve()
            relative = Path(source["path"]).relative_to(root)
            if not (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
                    and os.open in getattr(os, "supports_dir_fd", ())):
                # Windows has no openat/O_NOFOLLOW API. Open in binary mode,
                # then verify both file identity and the resolved pathname so
                # replacement during validation cannot redirect the response.
                path = Path(source["path"])
                expected = path.stat()
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                                     | getattr(os, "O_NONBLOCK", 0))
                try:
                    details = os.fstat(descriptor)
                    if (not stat.S_ISREG(details.st_mode) or not details.st_size
                            or path.resolve() != path
                            or (details.st_dev, details.st_ino) != (expected.st_dev, expected.st_ino)):
                        raise ValueError("The video path changed while opening the recording.")
                    handle = os.fdopen(descriptor, "rb")
                    descriptor = None
                    return handle, source, identifier
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(root, directory_flags)
            descriptor = None
            try:
                for component in relative.parts[:-1]:
                    child = os.open(component, directory_flags, dir_fd=directory)
                    os.close(directory)
                    directory = child
                descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory)
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode) or not details.st_size:
                    raise ValueError("The video recording is unavailable.")
                handle = os.fdopen(descriptor, "rb")
                descriptor = None
                return handle, source, identifier
            finally:
                os.close(directory)
                if descriptor is not None:
                    os.close(descriptor)

    def deletion_preview(self, session_id):
        with self.lifecycle_lock, self.lock:
            session = self.store.get_session(session_id)
            if session is None:
                raise KeyError(session_id)
            source = session["source"]
            path = self._source_path(source)
            root = self.root.resolve()
            source_path = Path(source["path"]).expanduser() if path is not None else None
            if source_path is not None and not source_path.is_absolute():
                source_path = self.root / source_path
            symlink = source_path is not None and any(part.is_symlink() for part in (source_path, *source_path.parents))
            managed = bool(path and not symlink and path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}
                           and any(path.is_relative_to(root / "data" / directory) for directory in ("uploads", "references")))
            exists = bool(path and path.is_file())
            shared = [row["id"] for row in self.store.list_session_sources()
                      if row["id"] != session_id and path is not None and self._source_path(row["source"]) == path]
            busy = self.state in {"running", "paused", "connecting"} or any(worker and worker.is_alive() for worker in (self.worker, self.reader))
            blocked = None
            if session["status"] == "recording":
                blocked = "This recording is still being captured or analyzed. Stop it before deleting."
            elif busy and (self.session_id == session_id or path is not None and self._source_path(self.source) == path):
                blocked = "This video is in use by the active source. Stop it before deleting."
            reason = ("No local video is attached." if path is None else
                      "The video file is already missing." if not exists else
                      "Only app-managed uploads and downloaded videos can be deleted." if not managed else
                      "Other recordings share this video; the file will be kept." if shared else
                      blocked if blocked else "This app-managed video is not shared by another recording.")
            return {"session_id": session_id,
                    "name": source.get("name", path.name if path else "Recording") if isinstance(source, dict) else str(source),
                    "can_delete": blocked is None, "blocked_reason": blocked,
                    "video": {"can_delete": bool(managed and exists and not shared and not blocked),
                              "reason": reason, "name": path.name if path else None, "path": str(path) if path else None,
                              "exists": exists, "managed": managed, "shared_session_ids": shared}}

    def _unlink_managed_video(self, path):
        # Traverse without following directory symlinks, including one introduced
        # after preview. Unlink remains confined to the project directory tree.
        relative = Path(path).relative_to(self.root.resolve())
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root.resolve(), flags)
        try:
            for directory in relative.parts[:-1]:
                child = os.open(directory, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            details = os.stat(relative.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(details.st_mode):
                raise OSError("The video path changed after confirmation.")
            os.unlink(relative.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)

    def delete_session(self, session_id, delete_video=False):
        if not isinstance(delete_video, bool):
            raise ValueError("delete_video must be a boolean.")
        with self.lifecycle_lock, self.lock:
            preview = self.deletion_preview(session_id)
            if not preview["can_delete"]:
                raise ValueError(preview["blocked_reason"])
            if delete_video and not preview["video"]["can_delete"]:
                raise ValueError(preview["video"]["reason"])
            self.store.delete_session(session_id)
            if self.session_id == session_id:
                self.session_id = self.source = None
                self.analyzer = SessionAnalyzer(retain_samples=False)
                self.history.clear()
                self.latest = self.preview = self.last_frame = None
                self.preview_time = None
                self.duration = self.error = self.last_received = None
                self.processing_ms = self.fps = self.source_time = 0.0
                self.arrivals.clear()
                self.state = "idle"
                self.sequence += 1
            deleted, warnings = False, []
            if delete_video:
                try:
                    self._unlink_managed_video(preview["video"]["path"])
                    deleted = True
                except OSError as error:
                    warnings.append("Recording deleted, but the video file could not be removed: " + str(error))
            return {"session_id": session_id, "deleted": True, "video_deleted": deleted,
                    "video_retained": preview["video"]["exists"] and not deleted, "warnings": warnings}

    def stop(self):
        with self.lifecycle_lock:
            self._stop()
        return self.status()

    def _stop(self):
        self.stop_event.set()
        proc = self.process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=12)
            if self.worker.is_alive():
                raise RuntimeError("The previous source is still closing. Try again in a few seconds.")
        if self.reader and self.reader.is_alive():
            self.reader.join(timeout=6)
            if self.reader.is_alive():
                raise RuntimeError("The previous decoder is still closing. Try again in a few seconds.")
        self.worker = None
        self.reader = None
        with self.lock:
            if self.session_id:
                self.analyzer.finalize()
                self._save(status="error" if self.error else "completed")
            if self.state != "idle":
                self.state = "ended"
            self.sequence += 1

    def control(self, action, position=None):
        if action == "stop":
            return self.stop()
        with self.lock:
            if not self.session_id:
                raise ValueError("Connect a source first.")
            if action == "pause":
                if self.state != "running":
                    raise ValueError("Only a running source can be paused.")
                self.pause_event.set()
                self.analyzer.discontinuity("paused")
                self.state = "paused"
            elif action == "resume":
                if self.state != "paused":
                    raise ValueError("Only a paused source can be resumed.")
                self.pause_event.clear()
                self.state = "running"
                self._next_gap = "resumed"
            elif action == "seek":
                if self.source["kind"] != "file" or self.state not in {"running", "paused"}:
                    raise ValueError("Seeking requires an active local video.")
                if position is None or position < 0 or (self.duration and position >= self.duration):
                    raise ValueError("Seek time is outside this video.")
                self.seek_to = float(position)
                self.analyzer.discontinuity("seek")
                self.history.clear()
            elif action == "mark_lap":
                if self.state != "running" or not self.latest or not self.latest.get("hud_visible"):
                    raise ValueError("A lap boundary needs a visible, running HUD.")
                self.analyzer.mark_lap()
                self._save()
            else:
                raise ValueError("Unknown playback action.")
            self.sequence += 1
        return self.status()

    def set_profile(self, profile):
        candidate = HUDExtractor(profile)
        with self.lock:
            self.profile = candidate.profile
            self.extractor = candidate
            self._calibration_revision = None
            (self.data_dir / "profile.json").write_text(json.dumps(self.profile, indent=2), encoding="utf-8")
            self.analyzer.discontinuity("calibration_changed")
            return self.profile

    def _save(self, status=None):
        if self.session_id:
            self.store.save_summary(self.session_id, self.analyzer.summary(), self.analyzer.events, self.analyzer.laps, status=status)
            if hasattr(self.extractor, "resolved_profile"):
                self.store.update_metadata(self.session_id, {"resolved_profile": self.extractor.resolved_profile(),
                                                             "calibration": self.extractor.calibration_status})
            self.store.flush()
        self._last_save = time.monotonic()

    def _process_frame(self, frame, stamp, expected_session=None):
        started = time.perf_counter()
        with self.lock:
            if expected_session is not None and expected_session != self.session_id:
                return False
            if self.stop_event.is_set() or self.pause_event.is_set():
                return False
            if self.last_received and time.monotonic() - self.last_received > 1.5:
                self.analyzer.discontinuity("source_stalled")
            if self._next_gap:
                self.analyzer.discontinuity(self._next_gap)
                self._next_gap = None
            sample = self.extractor.extract(frame, stamp)
            calibration = sample.get("calibration")
            if calibration and calibration.get("status") == "locked":
                revision = calibration.get("revision")
                if self._calibration_revision is not None and revision != self._calibration_revision:
                    self.analyzer.discontinuity("calibration_changed")
                self._calibration_revision = revision
            thumbnail = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
            if self._last_thumbnail is not None and np.array_equal(thumbnail, self._last_thumbnail):
                if self._unchanged_since is None:
                    self._unchanged_since = stamp
                if stamp - self._unchanged_since > 1.5:
                    sample.update(stale=True, brake=None, throttle=None, speed=None, gear=None, rpm=None,
                                  steering=None, steering_status="unavailable", steering_confidence=0,
                                  lap_time=None, lap_time_seconds=None, lap_valid=None,
                                  brake_highlighted=None, throttle_highlighted=None)
            else:
                self._unchanged_since = None
            self._last_thumbnail = thumbnail
            sample = self.analyzer.process(sample)
            sample["processing_ms"] = round((time.perf_counter() - started) * 1000, 3)
            sample["received_at"] = time.time()
            self.processing_ms = sample["processing_ms"]
            self.latest = sample
            self.history.append(sample)
            self.source_time = stamp
            now = time.monotonic()
            self.last_received = now
            self.arrivals.append(now)
            if len(self.arrivals) > 1:
                self.fps = (len(self.arrivals) - 1) / max(.001, self.arrivals[-1] - self.arrivals[0])
            self.state = "running"
            self.sequence += 1
            self.store.add_sample(self.session_id, sample)
            self.last_frame = frame
            self._update_preview(frame, stamp, now=now)
            if now - self._last_save > 2:
                self._save()
            return True

    def _update_preview(self, frame, stamp, now=None, force=False):
        # Local video uses native browser playback; retain inexpensive fallback
        # stills. Live sources need a smoother preview without encoding every
        # telemetry frame or upscaling low-resolution capture pixels.
        now = time.monotonic() if now is None else now
        interval = .3 if self.source and self.source.get("kind") == "file" else .1
        if not force and self.preview is not None and now - self._last_preview < interval:
            return
        width = min(frame.shape[1], 1280)
        picture = frame if width == frame.shape[1] else cv2.resize(
            frame, (width, max(1, round(frame.shape[0] * width / frame.shape[1]))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            self.preview = encoded.tobytes()
            self.preview_time = float(stamp)
        self._last_preview = now

    def browser_frame(self, content, stamp):
        with self.lock:
            if not self.source or self.source["kind"] != "browser" or self.state not in {"running", "paused"}:
                raise ValueError("Start direct capture before sending frames.")
            if self._browser_last_time is not None and stamp <= self._browser_last_time:
                return {"accepted": False, "reason": "duplicate_or_old_frame"}
            self._browser_last_time = stamp
            session_id = self.session_id
        image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size > 4096 * 2160 * 3:
            raise ValueError("Expected a JPEG frame up to 4096 x 2160.")
        accepted = self._process_frame(image, stamp, expected_session=session_id)
        return {"accepted": accepted, "reason": None if accepted else "source_changed_or_paused"}

    def _open_stream(self):
        if self.source["kind"] == "twitch":
            command = module_command("streamlink") + ["--stdout", "--loglevel", "error",
                       "--twitch-low-latency", "--hls-live-edge", "2", "--stream-timeout", "15",
                       "--http-timeout", "10", self.source["url"], "best"]
            log = open(self.data_dir / "streamlink.log", "wb")
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                            stdin=subprocess.DEVNULL, **subprocess_options())
            log.close()
            return av.open(self.process.stdout, options={"probesize": "1000000", "analyzeduration": "1000000"})
        return av.open(self.source["url"], timeout=(10, 5), options={"rw_timeout": "5000000"})

    def _decode_live(self, container, stop_event, frame_queue, reader_done, session_id):
        try:
            stream = container.streams.video[0]
            stream.thread_type = "SLICE"
            origin = None
            for frame in container.decode(stream):
                if stop_event.is_set():
                    break
                pts = float(frame.time) if frame.time is not None else time.monotonic()
                if origin is None:
                    origin = pts
                item = (frame, pts - origin)
                try:
                    frame_queue.put_nowait(item)
                except queue.Full:
                    try:
                        frame_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.dropped_frames += 1
                    frame_queue.put_nowait(item)
        except Exception as exc:
            if not stop_event.is_set() and self.session_id == session_id:
                self.error = f"Video stream interrupted: {type(exc).__name__}"
        finally:
            container.close()
            reader_done.set()

    def _run(self):
        try:
            if self.source["kind"] == "file":
                self._run_file()
            elif self.source.get("recorded"):
                # VODs must retain every sampled frame, even when downloads burst.
                self._run_recording(self._open_stream())
            else:
                self._reader_done = threading.Event()
                container = self._open_stream()
                self.reader = threading.Thread(target=self._decode_live, args=(container, self.stop_event, self.frame_queue, self._reader_done, self.session_id), daemon=True, name="video-decode")
                self.reader.start()
                while not self.stop_event.is_set():
                    try:
                        frame, stamp = self.frame_queue.get(timeout=.2)
                    except queue.Empty:
                        if self._reader_done.is_set():
                            break
                        continue
                    self._process_frame(frame.to_ndarray(format="bgr24"), stamp)
                if self.reader:
                    self.reader.join(timeout=6)
        except Exception as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.error = f"Could not read source ({type(exc).__name__}): {str(exc)[:220]}"
                    if self.source and self.source["kind"] == "twitch":
                        self.error = "Twitch could not be decoded. Check that the channel is live or the VOD is public. See data/streamlink.log; window capture is also available."
                    self.state = "error"
        finally:
            proc = self.process
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
            self.process = None
            with self.lock:
                self.analyzer.finalize()
                if self.state != "error":
                    self.state = "error" if self.error else "ended"
                self._save(status="error" if self.error else "completed")
                self.sequence += 1

    def _run_file(self):
        self._run_recording(av.open(self.source["path"]))

    def _run_recording(self, container):
        with container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            stream.codec_context.thread_count = 4
            self.duration = container.duration / 1e6 if container.duration else None
            iterator = iter(container.decode(stream))
            anchor = None
            last_stamp = None
            previous_decoded_stamp = None
            target = None
            while not self.stop_event.is_set():
                with self.lock:
                    seek = self.seek_to
                    self.seek_to = None
                if seek is not None:
                    container.seek(int(seek / float(stream.time_base)), stream=stream, backward=True)
                    iterator = iter(container.decode(stream))
                    anchor = last_stamp = None
                    previous_decoded_stamp = None
                    target = seek
                if self.pause_event.is_set() and target is None:
                    self.stop_event.wait(.05)
                    anchor = None
                    continue
                try:
                    frame = next(iterator)
                except StopIteration:
                    break
                stamp = float(frame.time or 0)
                if previous_decoded_stamp is not None and stamp < previous_decoded_stamp:
                    # Edited recordings can restart their source clock. Do not
                    # discard the following frames until the old clock catches up.
                    with self.lock:
                        self.analyzer.discontinuity("source_timestamp_reset")
                        self._last_thumbnail = self._unchanged_since = None
                    anchor = last_stamp = None
                previous_decoded_stamp = stamp
                if target is not None:
                    if stamp < target:
                        continue
                    target = None
                    if self.pause_event.is_set():
                        image = frame.to_ndarray(format="bgr24")
                        with self.lock:
                            self.source_time = stamp
                            self.last_frame = image
                            self._update_preview(image, stamp, force=True)
                            self.sequence += 1
                        continue
                if last_stamp is not None and stamp - last_stamp < 1 / 31:
                    continue
                if self.source.get("realtime", True):
                    if anchor is None:
                        anchor = time.monotonic() - stamp
                    delay = anchor + stamp - time.monotonic()
                    if delay > 0 and self.stop_event.wait(delay):
                        break
                self._process_frame(frame.to_ndarray(format="bgr24"), stamp)
                last_stamp = stamp

    def close(self):
        self.stop()
        self.store.close()
