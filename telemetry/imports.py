"""Independent, cancellable reference-video imports using the shared session store."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

import av
import cv2
import numpy as np
import requests

from .analysis import SessionAnalyzer
from .profiles import default_profile
from .vision import HUDExtractor
from .runtime import javascript_runtime_args, module_command, subprocess_options

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}
MAX_BYTES = 4 * 1024 ** 3
MAX_DURATION = 7200
MAX_FRAMES = MAX_DURATION * 31
MAX_ANALYSIS_SECONDS = 1800
MAX_DOWNLOAD_SECONDS = 1200
TERMINAL = {"completed", "cancelled", "error"}
TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "go.twitch.tv",
                "clips.twitch.tv", "player.twitch.tv"}


def _twitch_recording_url(parsed):
    """Accept one public archived video or clip, never a live channel/collection."""
    if parsed.hostname.lower() not in TWITCH_HOSTS or parsed.port not in {None, 80, 443}:
        raise ValueError("Enter a public Twitch VOD or clip link on twitch.tv.")
    path = parsed.path.rstrip("/")
    query = parse_qs(parsed.query)
    video_id = clip_id = None
    if parsed.hostname.lower() == "player.twitch.tv":
        value = query.get("video", [""])[0]
        if re.fullmatch(r"v?\d+", value):
            video_id = value.lstrip("v")
    elif parsed.hostname.lower() == "clips.twitch.tv":
        if path == "/embed":
            clip_id = query.get("clip", [""])[0]
        elif re.fullmatch(r"/(?:[A-Za-z0-9_]+/)?[A-Za-z0-9_-]+", path):
            clip_id = path.rsplit("/", 1)[-1]
    else:
        vod = re.fullmatch(r"/(?:videos|[A-Za-z0-9_]+/v(?:ideo)?)/(\d+)", path)
        clip = re.fullmatch(r"/(?:[A-Za-z0-9_]+/)?clip/([A-Za-z0-9_-]+)", path)
        if vod:
            video_id = vod.group(1)
        elif clip:
            clip_id = clip.group(1)
        elif re.fullmatch(r"/[A-Za-z0-9_]+/schedule", path):
            video_id = query.get("vodID", [""])[0]
    if video_id and re.fullmatch(r"\d+", video_id):
        return "https://www.twitch.tv/videos/" + video_id
    if clip_id and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", clip_id):
        return "https://clips.twitch.tv/" + clip_id
    raise ValueError("Use a public Twitch VOD link (/videos/...) or clip. Live channel links belong in Connect Source.")


class ImportCancelled(Exception):
    pass


class ReferenceImports:
    def __init__(self, engine):
        self.engine = engine
        self.root = engine.root
        self.store = engine.store
        self.lock = threading.RLock()
        self.jobs = {}
        self.worker = None
        self.stop_event = threading.Event()
        self.closed = False
        self.folder = self.root / "data/references"
        self.folder.mkdir(parents=True, exist_ok=True)

    def list(self):
        with self.lock:
            return copy.deepcopy(list(reversed(self.jobs.values())))

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return copy.deepcopy(self.jobs[job_id])

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def start(self, request):
        with self.engine.lifecycle_lock:
            return self._start(request)

    def _start(self, request):
        source = dict(request)
        if source.get("context") is not None:
            from .session_context import validate_context
            source["context"] = validate_context(source["context"])
        role = source.get("role", "reference")
        if not isinstance(role, str) or role not in {"current", "reference"}:
            raise ValueError("Import role must be current or reference.")
        source["role"] = role
        kind = source.get("kind")
        if kind == "file":
            source = self.engine.validate_source(source)
        elif kind in {"youtube", "twitch", "url"}:
            url = source.get("url") or ""
            if not isinstance(url, str):
                raise ValueError("Enter a public http(s) video URL.")
            url = url.strip()
            try:
                parsed = urlparse(url)
                parsed.port  # Reject malformed ports before launching a worker.
            except ValueError:
                raise ValueError("Enter a valid public http(s) video URL.") from None
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Enter a public http(s) video URL without embedded credentials.")
            if kind == "youtube" and parsed.hostname.lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
                raise ValueError("Enter a YouTube video link, or select a local recording.")
            if kind == "twitch" or kind == "url" and parsed.hostname.lower() in TWITCH_HOSTS:
                url = _twitch_recording_url(parsed)
                source["kind"] = "twitch"
            source["url"] = url
        else:
            raise ValueError("Reference imports support local files, YouTube videos, Twitch VODs/clips, or direct video URLs.")
        if request.get("name"):
            source["name"] = request["name"]
        profile = copy.deepcopy(source.pop("profile", None) or default_profile())
        profile["auto_calibrate"] = source.pop("auto_calibrate", True)
        profile = HUDExtractor(profile).profile
        with self.lock:
            if self.closed:
                raise ValueError("Reference importer is closing.")
            if self.worker and self.worker.is_alive():
                raise ValueError("One reference is already importing. Wait for it or cancel it first.")
            job_id = uuid.uuid4().hex
            self.stop_event = threading.Event()
            job = {"id": job_id, "state": "queued", "progress": 0, "session_id": None,
                   "error": None, "name": request.get("name") or source.get("name") or source.get("url"),
                   "source": source, "source_time": 0, "duration": None, "processed_frames": 0,
                   "calibration": None, "elapsed_seconds": 0, "realtime_factor": None, "role": role}
            self.jobs[job_id] = job
            while len(self.jobs) > 50:
                del self.jobs[next(iter(self.jobs))]
            self.worker = threading.Thread(target=self._run, args=(job_id, source, profile, self.stop_event),
                                           daemon=True, name="reference-import")
            self.worker.start()
            return copy.deepcopy(job)

    def deletion_block_reason(self, session_id, path=None):
        with self.lock:
            for job in self.jobs.values():
                if job["state"] in TERMINAL:
                    continue
                job_path = self.engine._source_path(job.get("source"))
                if job_path is None and job.get("session_id"):
                    session = self.store.get_session(job["session_id"])
                    job_path = self.engine._source_path(session["source"]) if session else None
                same_file = path is not None and job_path is not None and job_path == Path(path).resolve()
                if job.get("session_id") == session_id or same_file:
                    return "This recording or video is still being imported. Finish or cancel the import before deleting."
            return None

    def forget_session(self, session_id):
        with self.lock:
            if self.deletion_block_reason(session_id):
                raise ValueError("An active import cannot be removed.")
            for job in self.jobs.values():
                if job.get("session_id") == session_id:
                    job["session_id"] = None
                    job["recording_deleted"] = True

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["state"] not in TERMINAL:
                self.stop_event.set()
            return job

    @staticmethod
    def _check(stop):
        if stop.is_set():
            raise ImportCancelled()

    def _download_youtube(self, job_id, source, stop):
        return self._download_ytdlp(job_id, source, stop, provider="YouTube")

    def _download_twitch(self, job_id, source, stop):
        return self._download_ytdlp(job_id, source, stop, provider="Twitch")

    def _download_ytdlp(self, job_id, source, stop, *, provider):
        self._check(stop)
        directory = self.folder / job_id
        directory.mkdir()
        # Twitch's ordinary clip/HLS formats already contain audio. Selecting a
        # combined format preserves it without requiring an external ffmpeg merge.
        selector = ("b[height<=1080]/bv[height<=1080]" if provider == "Twitch" else
                    "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080]")
        command = module_command("yt_dlp") + ["--ignore-config", "--no-playlist",
                   "--socket-timeout", "15", "--retries", "1", "--fragment-retries", "1",
                   "--max-filesize", str(MAX_BYTES), "--match-filter", f"!is_live & duration > 0 & duration <= {MAX_DURATION}",
                   "--no-progress", "--write-info-json", "--no-warnings",
                   "-f", selector,
                   "-o", str(directory / "media.%(ext)s")]
        if provider == "Twitch":
            command.extend(["--downloader", "m3u8:native", "--hls-use-mpegts", "--fixup", "never"])
        else:
            command.extend(javascript_runtime_args())
        command.extend(["--", source["url"]])
        started = time.monotonic()
        with (directory / "download.log").open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, **subprocess_options())
            try:
                while process.poll() is None:
                    self._check(stop)
                    if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                        raise ValueError("Reference download exceeded 20 minutes. Import a local copy instead.")
                    if sum(path.stat().st_size for path in directory.iterdir() if path.is_file()) > MAX_BYTES:
                        raise ValueError("Reference exceeds the 4 GB import limit.")
                    stop.wait(.2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        if process.returncode:
            reason = ("The VOD or clip may have expired, require subscriber access, or be unavailable. "
                      if provider == "Twitch" else "The video may require sign-in or be unavailable. ")
            raise ValueError(provider + " download failed. " + reason + "Import a local copy; details are in data/references/" + job_id + "/download.log.")
        videos = [path for path in directory.iterdir() if path.suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise ValueError("No downloadable video was returned. Choose a public recording under two hours, or import a local copy.")
        if videos[0].stat().st_size > MAX_BYTES:
            raise ValueError("Reference exceeds the 4 GB import limit.")
        metadata_path = directory / "media.info.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        path = self._remux_twitch_hls(videos[0], stop, started) if provider == "Twitch" else videos[0]
        return path, metadata.get("title") or source["url"]

    def _remux_twitch_hls(self, path, stop, started):
        """Make native-downloaded Twitch MPEG-TS browser-playable without re-encoding.

        yt-dlp may retain an .mp4 extension on concatenated HLS transport-stream
        bytes. Detect the container, copy its video/audio packets to genuine MP4,
        and shift both streams by one common origin to retain A/V synchronization.
        """
        temporary = path.parent / "media.remux.mp4.part"
        target = path.with_suffix(".mp4")

        def check():
            self._check(stop)
            if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                raise ValueError("Reference download and preparation exceeded 20 minutes. Import a local copy instead.")
            if temporary.exists() and temporary.stat().st_size > MAX_BYTES:
                raise ValueError("Reference exceeds the 4 GB import limit.")

        try:
            check()
            with av.open(str(path)) as recording:
                if "mpegts" not in recording.format.name.split(","):
                    return path
                if not recording.streams.video:
                    raise ValueError("Twitch recording contains no video track.")
                streams = list(recording.streams.video[:1]) + list(recording.streams.audio[:1])
                starts = [stream.start_time * stream.time_base for stream in streams
                          if stream.start_time is not None and stream.time_base is not None]
                origin = min(starts) if starts else (recording.start_time or 0) / av.time_base
                with av.open(str(temporary), "w", format="mp4", options={"movflags": "+faststart"}) as output:
                    mapping = {stream.index: output.add_stream_from_template(stream) for stream in streams}
                    copied_video = False
                    for packet in recording.demux(streams):
                        check()
                        if packet.dts is None:
                            continue
                        offset = round(origin / packet.time_base)
                        if packet.pts is not None:
                            packet.pts -= offset
                            if packet.pts * packet.time_base > MAX_DURATION + 1:
                                raise ValueError("References must be under two hours; trim this recording before importing.")
                        packet.dts -= offset
                        copied_video |= packet.stream.type == "video"
                        packet.stream = mapping[packet.stream.index]
                        output.mux(packet)
                    if not copied_video:
                        raise ValueError("Twitch recording contains no playable video packets.")
            check()
            temporary.replace(target)
            if path != target:
                path.unlink()
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def _download_url(self, job_id, source, stop):
        extension = Path(urlparse(source["url"]).path).suffix.lower()
        if extension not in VIDEO_EXTENSIONS:
            raise ValueError("Use a direct video-file URL (MP4, MOV, MKV, or WebM), not a live playlist or webpage.")
        directory = self.folder / job_id
        directory.mkdir()
        target = directory / ("media" + extension)
        partial = target.with_suffix(extension + ".part")
        started = time.monotonic()
        try:
            with requests.get(source["url"], stream=True, timeout=(10, 15)) as reply:
                reply.raise_for_status()
                total = int(reply.headers.get("Content-Length", 0))
                if total > MAX_BYTES:
                    raise ValueError("Reference exceeds the 4 GB import limit.")
                received = 0
                with partial.open("wb") as output:
                    for chunk in reply.iter_content(256 * 1024):
                        self._check(stop)
                        received += len(chunk)
                        if received > MAX_BYTES or time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                            raise ValueError("Reference download exceeded the size or time limit.")
                        output.write(chunk)
                        self._update(job_id, progress=round(100 * received / total, 1) if total else None)
            partial.rename(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return target, Path(urlparse(source["url"]).path).name

    def _cleanup_download(self, job_id):
        directory = self.folder / job_id
        if directory.is_dir():
            for path in directory.iterdir():
                if path.is_file() and path.name.startswith("media.") and path.name != "media.info.json":
                    path.unlink(missing_ok=True)

    def _run(self, job_id, source, profile, stop):
        started = time.monotonic()
        session_id = None
        analyzer = SessionAnalyzer(retain_samples=False)
        extractor = None
        last_stamp = None
        source_span = 0.0
        count = 0

        def persist(status):
            analyzer.finalize()
            self.store.save_summary(session_id, analyzer.summary(), analyzer.events, analyzer.laps, status=status)
            metadata = {"profile": extractor.resolved_profile() if hasattr(extractor, "resolved_profile") else profile,
                        "calibration": getattr(extractor, "calibration_status", None),
                        "analysis_seconds": round(time.monotonic() - started, 3), "sample_rate_limit_hz": 30,
                        "source_span_seconds": source_span}
            self.store.update_metadata(session_id, metadata)
            return metadata

        try:
            self._check(stop)
            extractor = HUDExtractor(profile)
            if source["kind"] == "file":
                path, name = Path(source["path"]), source["name"]
            else:
                self._update(job_id, state="downloading", progress=None)
                download = {"youtube": self._download_youtube, "twitch": self._download_twitch,
                            "url": self._download_url}[source["kind"]]
                path, name = download(job_id, source, stop)
            self._check(stop)
            if path.stat().st_size > MAX_BYTES:
                raise ValueError("Reference exceeds the 4 GB import limit.")
            name = source.get("name") or name
            local_source = {"kind": "file", "path": str(path), "name": name, "realtime": False,
                            "role": source["role"], "original_url": source.get("url")}
            metadata = {"profile": profile, "measurement": "video-derived", "role": source["role"],
                        "import_id": job_id, "telemetry_context": source.get("context") or {}}
            if source.get("curated_reference"):
                local_source["reference_library_id"] = source["curated_reference"]["id"]
                metadata["reference_library"] = copy.deepcopy(source["curated_reference"])
            session_id = self.store.create_session(local_source, metadata)
            self._update(job_id, state="analyzing", progress=0, session_id=session_id, name=name)
            last_stamp = last_thumb = unchanged_since = None
            last_revision = None
            last_update = 0
            previous_decoded_stamp = None
            analysis_started = time.monotonic()
            with av.open(str(path)) as container:
                if not container.streams.video:
                    raise ValueError("Reference contains no video track.")
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                # Four decoder threads keep AV1/VP9 imports moving without the
                # unbounded worker pool selected by FFmpeg's automatic setting.
                stream.codec_context.thread_count = 4
                duration = float(stream.duration * stream.time_base) if stream.duration else (container.duration / 1e6 if container.duration else None)
                if duration is not None and (not math.isfinite(duration) or duration > MAX_DURATION):
                    raise ValueError("References must be under two hours; trim this recording before importing.")
                if stream.codec_context.width * stream.codec_context.height > 4096 * 2160:
                    raise ValueError("Reference resolution exceeds 4K; import a 1080p or 4K copy.")
                self._update(job_id, duration=duration)
                for frame in container.decode(stream):
                    self._check(stop)
                    stamp = float(frame.time or 0)
                    if not math.isfinite(stamp):
                        raise ValueError("Reference contains an invalid video timestamp.")
                    if previous_decoded_stamp is not None:
                        if stamp < previous_decoded_stamp:
                            analyzer.discontinuity("source_timestamp_reset")
                            last_stamp = last_thumb = unchanged_since = None
                        else:
                            source_span += stamp - previous_decoded_stamp
                    previous_decoded_stamp = stamp
                    if source_span > MAX_DURATION or count >= MAX_FRAMES:
                        raise ValueError("Reference exceeds the two-hour analysis limit.")
                    if time.monotonic() - analysis_started > MAX_ANALYSIS_SECONDS:
                        raise ValueError("Reference analysis exceeded 30 minutes; import a shorter recording.")
                    if last_stamp is not None and stamp - last_stamp < 1 / 31:
                        continue
                    last_stamp = stamp
                    tick = time.perf_counter()
                    picture = frame.to_ndarray(format="bgr24")
                    scale = min(1, 1920 / picture.shape[1], 1080 / picture.shape[0])
                    if scale < 1:
                        picture = cv2.resize(picture, (round(picture.shape[1] * scale), round(picture.shape[0] * scale)), interpolation=cv2.INTER_AREA)
                    sample = extractor.extract(picture, stamp)
                    self._check(stop)
                    calibration = sample.get("calibration")
                    if calibration and calibration.get("status") == "locked":
                        revision = calibration.get("revision")
                        if last_revision is not None and revision != last_revision:
                            analyzer.discontinuity("calibration_changed")
                        last_revision = revision
                    thumb = cv2.resize(picture, (96, 54), interpolation=cv2.INTER_AREA)
                    if last_thumb is not None and np.array_equal(thumb, last_thumb):
                        if unchanged_since is None:
                            unchanged_since = stamp
                        if stamp - unchanged_since > 1.5:
                            sample["stale"] = True
                            for key in ("brake", "throttle", "speed", "gear", "steering", "rpm", "lap_time", "lap_time_seconds", "lap_valid"):
                                sample[key] = None
                            sample.update(steering_status="unavailable", steering_confidence=0)
                    else:
                        unchanged_since = None
                    last_thumb = thumb
                    sample = analyzer.process(sample)
                    sample["processing_ms"] = round((time.perf_counter() - tick) * 1000, 3)
                    self.store.add_sample(session_id, sample)
                    count += 1
                    now = time.monotonic()
                    if now - last_update > .5:
                        self._update(job_id, source_time=stamp, processed_frames=count, calibration=calibration,
                                     progress=min(99.9, round(100 * source_span / duration, 1)) if duration else None,
                                     elapsed_seconds=round(now - started, 2))
                        self.store.save_summary(session_id, analyzer.summary(), analyzer.events, analyzer.laps)
                        last_update = now
                    # Yield CPU to the live pipeline when both recordings are being processed.
                    if self.engine.state == "running":
                        stop.wait(.002)
            self._check(stop)
            metadata = persist("completed")
            elapsed = time.monotonic() - started
            self._update(job_id, state="completed", progress=100, source_time=last_stamp or 0,
                         processed_frames=count, elapsed_seconds=round(elapsed, 2), calibration=metadata["calibration"],
                         realtime_factor=round(source_span / elapsed, 2) if elapsed else None)
        except ImportCancelled:
            try:
                if session_id:
                    persist("interrupted")
                self._update(job_id, state="cancelled")
            except Exception as exc:
                self._update(job_id, state="error", error="Could not finalize cancelled import: " + str(exc)[:400])
        except Exception as exc:
            error = str(exc)[:400]
            try:
                if session_id:
                    persist("error")
            except Exception as save_error:
                error += "; could not finalize session: " + str(save_error)[:100]
            self._update(job_id, state="error", error=error)
        finally:
            self._update(job_id, processed_frames=count, source_time=last_stamp or 0,
                         elapsed_seconds=round(time.monotonic() - started, 2))
            if session_id is None and source["kind"] != "file":
                try:
                    self._cleanup_download(job_id)
                except OSError:
                    pass

    def close(self):
        with self.lock:
            self.closed = True
            self.stop_event.set()
            worker = self.worker
        if worker and worker.is_alive():
            worker.join(timeout=25)
            if worker.is_alive():
                raise RuntimeError("Reference importer is still closing.")
