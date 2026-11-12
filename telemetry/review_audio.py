"""On-demand audio-only preparation for saved recordings, independent of telemetry."""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

import av
from .runtime import javascript_runtime_args, module_command, subprocess_options


MAX_BYTES = 256 * 1024 ** 2
MAX_DURATION = 7200
MAX_SECONDS = 600


class AudioCancelled(Exception):
    pass


class AudioUnavailable(ValueError):
    pass


class AudioBusy(ValueError):
    pass


class ReviewAudio:
    def __init__(self, engine):
        self.engine = engine
        self.root = engine.root.resolve()
        self.folder = self.root / "data/review-audio"
        self.lock = threading.RLock()
        self.jobs = {}
        self.worker = None
        self.stop_event = threading.Event()
        self.closed = False

    @staticmethod
    def _youtube_url(source):
        url = source.get("original_url") or ""
        try:
            parsed = urlparse(url)
            allowed = (parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
                       and parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
                       and parsed.port in {None, 80, 443})
        except ValueError:
            return None
        if not allowed:
            return None
        if parsed.hostname == "youtu.be":
            identifier = parsed.path.strip("/")
        elif parsed.path == "/watch":
            identifier = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            identifier = parts[1] if len(parts) == 2 and parts[0] in {"shorts", "live", "embed"} else ""
        if re.fullmatch(r"[a-zA-Z0-9_-]{11}", identifier):
            return "https://www.youtube.com/watch?v=" + identifier
        return None

    def _source(self, session_id):
        session = self.engine.store.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        source = session["source"]
        if not isinstance(source, dict) or source.get("kind") != "file":
            raise AudioUnavailable("This session has no saved recording with audio.")
        try:
            source = self.engine.validate_source(source)
            details = Path(source["path"]).stat()
        except (OSError, ValueError):
            raise AudioUnavailable("The original recording is unavailable. Restore it before enabling audio.")
        signature = hashlib.sha256(json.dumps([source["path"], details.st_size, details.st_mtime_ns,
                                               source.get("original_url")], sort_keys=True).encode()).hexdigest()
        return source, signature

    def _directory(self, session_id, create=False):
        directory = self.folder / hashlib.sha256(session_id.encode()).hexdigest()
        for path in (self.folder.parent, self.folder, directory):
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("The review audio cache must stay inside the project.")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _result(session_id, state="idle", message=None, **values):
        return {"session_id": session_id, "state": state, "message": message, "url": None,
                "source_start": 0, "duration": None, **values}

    def _cached(self, session_id, signature):
        directory = self._directory(session_id)
        target, metadata = directory / "audio.m4a", directory / "audio.json"
        if not target.is_file() or target.is_symlink() or not metadata.is_file() or metadata.is_symlink():
            return None
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
            start, duration = float(value["source_start"]), float(value["duration"])
            if (value["signature"] != signature or not math.isfinite(start) or not math.isfinite(duration)
                    or not 0 < duration <= MAX_DURATION + 1 or not 0 < target.stat().st_size <= MAX_BYTES):
                return None
            return self._result(session_id, "ready", url=f"/api/sessions/{session_id}/audio/media",
                                source_start=start, duration=duration)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def status(self, session_id):
        with self.engine.lifecycle_lock, self.lock:
            try:
                _, signature = self._source(session_id)
            except AudioUnavailable as error:
                return self._result(session_id, "unavailable", str(error))
            cached = self._cached(session_id, signature)
            if cached:
                return cached
            job = self.jobs.get(session_id)
            if job and job["signature"] == signature:
                return copy.deepcopy(job["result"])
            return self._result(session_id)

    def prepare(self, session_id):
        with self.engine.lifecycle_lock, self.lock:
            current = self.status(session_id)
            if current["state"] in {"ready", "preparing", "unavailable"}:
                return current
            if self.closed:
                raise AudioBusy("Review audio is shutting down.")
            if self.worker and self.worker.is_alive():
                raise AudioBusy("Audio is being prepared for another recording. Try again in a moment.")
            source, signature = self._source(session_id)
            directory = self._directory(session_id, create=True)
            stop = self.stop_event = threading.Event()
            job = {"signature": signature, "stop": stop,
                   "result": self._result(session_id, "preparing", "Preparing recording audio...")}
            self.jobs[session_id] = job
            self.worker = threading.Thread(target=self._run, args=(session_id, source, directory, job),
                                           daemon=True, name="review-audio")
            self.worker.start()
            return copy.deepcopy(job["result"])

    @staticmethod
    def _check(stop, started):
        if stop.is_set():
            raise AudioCancelled()
        if time.monotonic() - started > MAX_SECONDS:
            raise ValueError("Audio preparation exceeded ten minutes. Try a local recording with audio.")

    def _download(self, url, directory, stop, started):
        command = module_command("yt_dlp") + ["--ignore-config", "--no-playlist", "--no-progress",
                   "--no-warnings", "--socket-timeout", "15", "--retries", "1", "--fragment-retries", "1",
                   "--max-filesize", str(MAX_BYTES), "--match-filter", "!is_live & duration <= 7200",
                   "-f", "ba[ext=m4a]/ba", "-o", str(directory / "download.%(ext)s")]
        command.extend(javascript_runtime_args())
        command.extend(["--", url])
        with (directory / "download.log").open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, **subprocess_options())
            try:
                while process.poll() is None:
                    self._check(stop, started)
                    if sum(path.stat().st_size for path in directory.iterdir() if path.is_file()) > MAX_BYTES:
                        raise ValueError("The audio download exceeds the 256 MB limit.")
                    stop.wait(.15)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        self._check(stop, started)
        if process.returncode:
            raise ValueError("Audio could not be downloaded from YouTube. Try again or import a local copy with audio.")
        files = [path for path in directory.iterdir()
                 if path.name.startswith("download.") and path.suffix in {".m4a", ".mp4", ".webm", ".opus", ".ogg", ".mp3", ".aac"}]
        if len(files) != 1 or files[0].stat().st_size > MAX_BYTES:
            raise ValueError("YouTube did not return a supported audio track within the 256 MB limit.")
        return files[0]

    def _convert(self, path, output, stop, started):
        with av.open(str(path)) as recording:
            if not recording.streams.audio:
                raise AudioUnavailable("This recording has no audio track.")
            stream = recording.streams.audio[0]
            stream.thread_count = 1
            if stream.duration and stream.time_base and float(stream.duration * stream.time_base) > MAX_DURATION + 1:
                raise AudioUnavailable("Review audio supports recordings up to two hours long.")
            resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
            with av.open(str(output), mode="w", format="ipod", options={"movflags": "+faststart"}) as target:
                audio = target.add_stream("aac", rate=48000)
                audio.layout = "stereo"
                audio.bit_rate = 128000
                audio.codec_context.thread_count = 1
                origin, last_end = None, 0

                def encode(frame):
                    nonlocal origin, last_end
                    self._check(stop, started)
                    if origin is None:
                        origin = float(frame.time or 0)
                    timestamp = round((float(frame.time) - origin) * 48000) if frame.time is not None else last_end
                    frame.pts = max(timestamp, last_end)
                    frame.time_base = Fraction(1, 48000)
                    last_end = frame.pts + frame.samples
                    if last_end / 48000 > MAX_DURATION + 1:
                        raise AudioUnavailable("Review audio supports recordings up to two hours long.")
                    for packet in audio.encode(frame):
                        target.mux(packet)
                    if output.exists() and output.stat().st_size > MAX_BYTES:
                        raise ValueError("The prepared audio exceeds the 256 MB limit.")

                for frame in recording.decode(stream):
                    for converted in resampler.resample(frame):
                        encode(converted)
                for converted in resampler.resample(None):
                    encode(converted)
                for packet in audio.encode(None):
                    target.mux(packet)
                if origin is None or not last_end:
                    raise AudioUnavailable("This recording has no playable audio samples.")
        return {"source_start": origin, "duration": last_end / 48000}

    def _run(self, session_id, source, directory, job):
        stop, started = job["stop"], time.monotonic()
        temporary = directory / uuid.uuid4().hex
        try:
            temporary.mkdir()
            path = Path(source["path"])
            self._check(stop, started)
            with av.open(str(path)) as recording:
                embedded = bool(recording.streams.audio)
            if not embedded:
                url = self._youtube_url(source)
                if not url:
                    raise AudioUnavailable("This recording has no audio track. Import a local copy that includes sound.")
                with self.lock:
                    job["result"]["message"] = "Downloading the recording's audio track..."
                path = self._download(url, temporary, stop, started)
            output = temporary / "audio.m4a"
            timing = self._convert(path, output, stop, started)
            with self.engine.lifecycle_lock, self.lock:
                self._check(stop, started)
                if self.jobs.get(session_id) is not job or self._source(session_id)[1] != job["signature"]:
                    raise AudioCancelled()
                output.replace(directory / "audio.m4a")
                metadata = temporary / "audio.json"
                metadata.write_text(json.dumps({"signature": job["signature"], **timing}), encoding="utf-8")
                metadata.replace(directory / "audio.json")
                job["result"] = self._result(session_id, "ready", url=f"/api/sessions/{session_id}/audio/media", **timing)
        except AudioCancelled:
            pass
        except Exception as error:
            with self.lock:
                if self.jobs.get(session_id) is job and not stop.is_set():
                    message = str(error) if isinstance(error, ValueError) else "The recording's audio could not be prepared. Try a supported local recording."
                    job["result"] = self._result(session_id, "unavailable" if isinstance(error, AudioUnavailable) else "error", message)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            if stop.is_set():
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def open_media(self, session_id):
        with self.engine.lifecycle_lock, self.lock:
            if self.status(session_id)["state"] != "ready":
                raise AudioUnavailable("Audio is not ready for this recording.")
            target = self._directory(session_id) / "audio.m4a"
            if target.is_symlink():
                raise AudioUnavailable("The cached audio is unavailable.")
            return target.open("rb")

    def forget_session(self, session_id):
        with self.engine.lifecycle_lock, self.lock:
            job = self.jobs.pop(session_id, None)
            if job:
                job["stop"].set()
            directory = self._directory(session_id)
            for name in ("audio.m4a", "audio.json"):
                (directory / name).unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass

    def close(self):
        with self.lock:
            self.closed = True
            self.stop_event.set()
            worker = self.worker
        if worker:
            worker.join(timeout=8)
