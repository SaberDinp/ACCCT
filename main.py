"""ACCC Telemetry: run with .venv/bin/python main.py."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import csv
from email.utils import formatdate
from functools import lru_cache
import io
import math
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlparse
from typing import Literal

import av
import cv2
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn
from starlette.background import BackgroundTask

from telemetry.engine import TelemetryEngine
from telemetry.imports import ReferenceImports
from telemetry.comparison import compare_session_ranges, trace_session_range
from telemetry.tracks import get_track, list_tracks
from telemetry.lap_detection import LapDetectionJobs, timer_candidates
from telemetry.review_audio import AudioBusy, AudioUnavailable, ReviewAudio
from telemetry.catalog import catalog
from telemetry.session_context import validate_context, session_context, comparison_context, enrich_comparison
from telemetry.validation import coverage_report

ROOT = Path(__file__).resolve().parent


class SourceRequest(BaseModel):
    kind: str
    path: str | None = None
    url: str | None = None
    realtime: bool = False
    context: dict | None = None


class ControlRequest(BaseModel):
    action: str
    time: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ImportRequest(BaseModel):
    kind: str
    path: str | None = None
    url: str | None = None
    name: str | None = Field(default=None, max_length=180)
    auto_calibrate: bool = True
    profile: dict | None = None
    role: Literal["current", "reference"] = "reference"
    context: dict | None = None


class ComparisonRequest(BaseModel):
    reference_session_id: str
    current_session_id: str
    reference_range: dict | None = None
    current_range: dict | None = None
    axis: str = "distance"
    align_start: bool = False
    align_end: bool = False
    confirm_same_track: bool = False
    alignment_mode: Literal["raw", "lap_endpoints", "landmarks"] = "raw"
    landmarks: list[dict] = Field(default_factory=list, max_length=32)
    max_points: int = Field(default=1200, ge=10, le=5000)


class TraceRequest(BaseModel):
    session_id: str
    range: dict | None = None
    axis: Literal["distance", "elapsed"] = "distance"
    max_points: int = Field(default=1200, ge=10, le=5000)


class ReviewedLandmark(BaseModel):
    marker_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    time: float = Field(ge=0, le=172800, allow_inf_nan=False)
    label: str = Field(default="Track landmark", max_length=120)


class ReviewedLap(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    lap_number: int = Field(default=1, ge=1, le=1000)
    start_time: float = Field(ge=0, le=172800, allow_inf_nan=False)
    end_time: float = Field(gt=0, le=172800, allow_inf_nan=False)
    segment_id: int | None = None
    valid: bool | None = None
    boundaries_verified: bool = False
    start_marker: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    end_marker: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    landmarks: list[ReviewedLandmark] = Field(default_factory=list, max_length=32)
    timing_uncertainty_s: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    notes: str = Field(default="", max_length=2000)


class ReviewedLapRequest(BaseModel):
    lap: ReviewedLap
    make_default: bool = True


class LapDetectionRequest(BaseModel):
    session_id: str
    anchor_time: float = Field(ge=0, le=172800, allow_inf_nan=False)
    search_start: float | None = Field(default=None, ge=0, le=172800, allow_inf_nan=False)
    search_end: float | None = Field(default=None, ge=0, le=172800, allow_inf_nan=False)
    roi: list[float] | None = Field(default=None, min_length=4, max_length=4)
    min_lap_seconds: float | None = Field(default=None, ge=10, le=1800, allow_inf_nan=False)
    max_lap_seconds: float | None = Field(default=None, ge=10, le=1800, allow_inf_nan=False)


class LandmarkDetectionRequest(BaseModel):
    anchor_session_id: str
    target_session_id: str
    anchor_time: float = Field(ge=0, le=172800, allow_inf_nan=False)
    search_start: float = Field(ge=0, le=172800, allow_inf_nan=False)
    search_end: float = Field(ge=0, le=172800, allow_inf_nan=False)
    roi: list[float] | None = Field(default=None, min_length=4, max_length=4)


def ranged_media(handle, request, media_type, filename):
    """Stream one open local file with bounded reads and browser seek support."""
    details = os.fstat(handle.fileno())
    size = details.st_size
    etag = f'"{details.st_dev:x}-{details.st_ino:x}-{size:x}-{details.st_mtime_ns:x}"'
    modified = formatdate(details.st_mtime, usegmt=True)
    start, end, status = 0, size - 1, 200
    range_header = request.headers.get("range")
    if request.headers.get("if-range") not in {None, etag, modified}:
        range_header = None
    if range_header:
        match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", range_header.strip()) if len(range_header) <= 64 else None
        valid = bool(match and any(match.groups()) and all(len(value) <= 20 for value in match.groups()))
        if valid:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            else:
                suffix = int(last)
                start = max(0, size - suffix)
                valid = suffix > 0
            valid = valid and start <= end and start < size
        if not valid:
            handle.close()
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
        status = 206
    length = max(0, end - start + 1)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "ETag": etag,
               "Last-Modified": modified, "Content-Disposition": f'inline; filename="{filename}"'}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        handle.close()
        return Response(status_code=status, headers=headers, media_type=media_type)

    def chunks():
        try:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(chunks(), status_code=status, headers=headers, media_type=media_type,
                             background=BackgroundTask(handle.close))


def create_app(root=ROOT, database=None, port=8000):
    engine = TelemetryEngine(Path(root), database)
    imports = ReferenceImports(engine)
    lap_detection = LapDetectionJobs()
    review_audio = ReviewAudio(engine)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(lap_detection.close)
        await asyncio.to_thread(imports.close)
        await asyncio.to_thread(review_audio.close)
        await asyncio.to_thread(engine.close)

    app = FastAPI(title="ACCC Telemetry", lifespan=lifespan)
    app.state.engine = engine
    app.state.imports = imports
    app.state.lap_detection = lap_detection
    app.state.review_audio = review_audio

    @app.middleware("http")
    async def local_control(request: Request, call_next):
        peer = request.client.host if request.client else ""
        if peer not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "ACCC Telemetry is available locally on the analysis computer."}, status_code=403)
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin control is not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # The local UI is shipped with the app, and URLs remain stable across
        # upgrades. Do not let a browser reuse a previous release's interface.
        if request.url.path == "/" or request.url.path.startswith(("/api/", "/static/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/index.html")

    @app.get("/api/status")
    def status():
        return engine.status()

    @app.get("/api/tracks")
    def tracks():
        return list_tracks()

    @app.get("/api/catalog")
    def acc_catalog():
        return catalog()

    @app.get("/api/validation")
    def validation_coverage():
        return coverage_report(Path(root) / "data/validation")

    @app.get("/api/tracks/{track_id}")
    def track_layout(track_id: str):
        try:
            return get_track(track_id)
        except KeyError:
            raise HTTPException(404, "Track layout not found.")

    @app.get("/api/sources")
    def sources():
        paths = [p for p in Path(root).iterdir() if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}]
        uploads = Path(root) / "data/uploads"
        if uploads.exists():
            paths.extend(p for p in uploads.iterdir() if p.is_file())
        references = Path(root) / "data/references"
        if references.exists():
            paths.extend(p for p in references.rglob("*") if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"} and p.is_file())
        return [{"name": p.name, "path": str(p.relative_to(root))} for p in sorted(paths)]

    @app.post("/api/source")
    def source(body: SourceRequest):
        if body.kind != "file":
            raise ValueError("Analyze a saved recording. Live capture and live streams are not supported.")
        return engine.start(body.model_dump(exclude_none=True))

    @app.get("/api/imports")
    def list_imports():
        return imports.list()

    @app.post("/api/imports")
    def start_import(body: ImportRequest):
        return imports.start(body.model_dump(exclude_none=True))

    @app.get("/api/imports/{job_id}")
    def import_status(job_id: str):
        try:
            return imports.get(job_id)
        except KeyError:
            raise HTTPException(404, "Reference import not found.")

    @app.post("/api/imports/{job_id}/cancel")
    def cancel_import(job_id: str):
        try:
            return imports.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "Reference import not found.")

    @app.post("/api/control")
    def control(body: ControlRequest):
        return engine.control(body.action, body.time)

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".ts"}:
            raise ValueError("Choose a supported video file.")
        folder = Path(root) / "data/uploads"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{uuid.uuid4().hex[:12]}{extension}"
        total = 0
        try:
            with target.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > 4 * 1024 ** 3:
                        raise ValueError("Video exceeds the 4 GB upload limit; place it in the project folder.")
                    await asyncio.to_thread(output.write, chunk)
            with av.open(str(target)) as video:
                if not video.streams.video:
                    raise ValueError("No video track was found.")
        except Exception:
            target.unlink(missing_ok=True)
            raise ValueError("Could not import this video. Use a valid video under 4 GB.")
        finally:
            await file.close()
        return {"path": str(target.relative_to(root)), "name": file.filename}

    @app.get("/api/preview.jpg")
    def preview():
        with engine.lock:
            if not engine.preview:
                raise HTTPException(404, "No source frame yet.")
            headers = {"X-Source-Time": f"{engine.preview_time:.6f}"} if engine.preview_time is not None else {}
            return Response(engine.preview, media_type="image/jpeg", headers=headers)

    video_probes = {}

    def video_probe(handle, source):
        details = os.fstat(handle.fileno())
        key = (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)
        with engine.lock:
            cached = video_probes.get(key)
        if cached is not None:
            return dict(cached)
        # Probe the descriptor already validated by the engine, never a second
        # pathname lookup that could follow a replaced symlink.
        with os.fdopen(os.dup(handle.fileno()), "rb") as probe:
            with av.open(probe) as container:
                if not container.streams.video:
                    raise ValueError("The recording has no video track.")
                stream = container.streams.video[0]
                duration = (float(stream.duration * stream.time_base) if stream.duration is not None else
                            float(container.duration / av.time_base) if container.duration is not None else None)
                media_types = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
                               ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
                               ".ts": "video/mp2t"}
                result = {"source_start": float((stream.start_time or 0) * stream.time_base),
                          "duration": duration, "width": stream.width, "height": stream.height,
                          "fps": float(stream.average_rate) if stream.average_rate else None,
                          "video_codec": stream.codec_context.name,
                          "media_type": media_types[Path(source["path"]).suffix.lower()]}
        handle.seek(0)
        with engine.lock:
            if len(video_probes) >= 32:
                video_probes.pop(next(iter(video_probes)))
            video_probes[key] = result
        return dict(result)

    def open_video(session_id=None, expected_session_id=None):
        try:
            return engine.open_video_media(session_id, expected_session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.")
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        except (ValueError, OSError):
            raise HTTPException(404, "This source has no available local video.")

    def video_metadata(session_id=None, expected_session_id=None):
        try:
            handle, source, identifier = open_video(session_id, expected_session_id)
        except HTTPException as error:
            # Missing sessions and source switches are errors. A known live
            # source has a normal unavailable result so the UI can show JPEGs.
            known = session_id is None or engine.store.get_session(session_id) is not None
            if error.status_code != 404 or not known:
                raise
            return {"state": "unavailable", "session_id": session_id or engine.session_id, "url": None,
                    "message": error.detail, "source_start": 0, "duration": None,
                    "width": None, "height": None, "fps": None, "video_codec": None, "media_type": None}
        try:
            with handle:
                result = video_probe(handle, source)
        except (ValueError, OSError, av.error.FFmpegError):
            return {"state": "unavailable", "session_id": identifier, "url": None,
                    "message": "The original video cannot be decoded.", "source_start": 0, "duration": None,
                    "width": None, "height": None, "fps": None, "video_codec": None, "media_type": None}
        return {"state": "ready", "session_id": identifier, "message": None,
                "url": f"/api/sessions/{identifier}/video/media" if identifier else "/api/source/video/media", **result}

    def video_media(request, session_id=None, expected_session_id=None):
        handle, source, _ = open_video(session_id, expected_session_id)
        try:
            result = video_probe(handle, source)
            return ranged_media(handle, request, result["media_type"], "recording" + Path(source["path"]).suffix.lower())
        except (ValueError, OSError, av.error.FFmpegError):
            handle.close()
            raise HTTPException(404, "The original video cannot be decoded.")

    @app.get("/api/source/video")
    def source_video_status(session_id: str | None = None):
        return video_metadata(expected_session_id=session_id)

    @app.api_route("/api/source/video/media", methods=["GET", "HEAD"])
    def source_video_media(request: Request, session_id: str | None = None):
        return video_media(request, expected_session_id=session_id)

    @app.get("/api/sessions/{session_id}/video")
    def session_video_status(session_id: str):
        return video_metadata(session_id)

    @app.api_route("/api/sessions/{session_id}/video/media", methods=["GET", "HEAD"])
    def session_video_media(session_id: str, request: Request):
        return video_media(request, session_id)

    @app.get("/api/profile")
    def profile():
        return engine.profile

    @app.put("/api/profile")
    def put_profile(body: dict):
        return engine.set_profile(body)

    @app.get("/api/history")
    def history(seconds: float = 30):
        if not 0 < seconds <= 600:
            raise ValueError("History window must be between 0 and 600 seconds.")
        with engine.lock:
            cutoff = engine.source_time - seconds
            return [sample for sample in engine.history if sample["time"] >= cutoff]

    @app.get("/api/sessions")
    def sessions():
        values = engine.store.list_sessions()
        for value in values:
            value["context"] = session_context(value)
        return values

    def find_session(session_id):
        with engine.lock:
            if session_id == engine.session_id:
                engine._save()
            value = engine.store.get_session(session_id)
        if not value:
            raise HTTPException(404, "Session not found.")
        value["context"] = session_context(value)
        return value

    @app.patch("/api/sessions/{session_id}/context")
    def save_context(session_id: str, body: dict):
        context = validate_context(body)
        with engine.lifecycle_lock:
            value = find_session(session_id)
            if value["status"] == "recording":
                raise ValueError("Finish recording before changing car or circuit labels.")
            engine.store.update_metadata(session_id, {"telemetry_context": context})
            return {"context": session_context(find_session(session_id))}

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str):
        value = find_session(session_id)
        return {"session": value, "samples": engine.store.get_samples(session_id),
                "summary": value.get("summary", {}), "events": value.get("events", []), "laps": value.get("laps", [])}

    def deletion_preview(session_id):
        try:
            preview = engine.deletion_preview(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.")
        active = lap_detection.active_session_ids()
        reason = imports.deletion_block_reason(session_id, preview["video"]["path"])
        if session_id in active or active.intersection(preview["video"]["shared_session_ids"]):
            reason = "This video is in use by a visual search. Finish or cancel the search before deleting."
        if reason:
            preview.update(can_delete=False, blocked_reason=reason)
            preview["video"].update(can_delete=False, reason=reason)
        return preview

    @app.get("/api/sessions/{session_id}/deletion")
    def session_deletion_preview(session_id: str):
        with engine.lifecycle_lock, imports.lock:
            return deletion_preview(session_id)

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str, delete_video: bool = False):
        # Recheck under the same lifecycle lock used by source and search starts.
        with engine.lifecycle_lock, imports.lock:
            preview = deletion_preview(session_id)
            if not preview["can_delete"]:
                raise HTTPException(409, preview["blocked_reason"])
            if delete_video and not preview["video"]["can_delete"]:
                raise HTTPException(409, preview["video"]["reason"])
            result = engine.delete_session(session_id, delete_video=delete_video)
            imports.forget_session(session_id)
            try:
                review_audio.forget_session(session_id)
            except (OSError, ValueError):
                result["warnings"].append("Recording deleted, but its cached review audio could not be removed.")
            saved_frame.cache_clear()
            refined_laps.cache_clear()
            return result

    @app.get("/api/sessions/{session_id}/audio")
    def session_audio_status(session_id: str):
        try:
            return review_audio.status(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.")

    @app.post("/api/sessions/{session_id}/audio")
    def prepare_session_audio(session_id: str):
        try:
            return review_audio.prepare(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.")
        except AudioBusy as error:
            raise HTTPException(409, str(error))

    @app.api_route("/api/sessions/{session_id}/audio/media", methods=["GET", "HEAD"])
    def session_audio_media(session_id: str, request: Request):
        try:
            handle = review_audio.open_media(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found.")
        except (AudioUnavailable, OSError):
            raise HTTPException(404, "Audio is not ready for this recording.")
        return ranged_media(handle, request, "audio/mp4", "review-audio.m4a")

    @lru_cache(maxsize=16)
    def refined_laps(session_id, updated_at):
        return timer_candidates(engine.store.get_samples(session_id))

    @app.get("/api/sessions/{session_id}/lap-candidates")
    def lap_candidates(session_id: str):
        value = find_session(session_id)
        return refined_laps(session_id, value["updated_at"])

    @app.post("/api/lap-detection")
    def detect_lap(body: LapDetectionRequest):
        with engine.lifecycle_lock:
            value = find_session(body.session_id)
            source = value["source"]
            if not isinstance(source, dict) or source.get("kind") != "file":
                raise ValueError("Visual lap search requires an original local recording.")
            source = engine.validate_source(source)
            settings = body.model_dump(exclude={"session_id"}, exclude_none=True)
            settings["track_id"] = session_context(value).get("track_id")
            return lap_detection.start(source["path"], body.session_id, settings)

    @app.post("/api/landmark-detection")
    def detect_landmark(body: LandmarkDetectionRequest):
        with engine.lifecycle_lock:
            sources = []
            for session_id in (body.anchor_session_id, body.target_session_id):
                source = find_session(session_id)["source"]
                if not isinstance(source, dict) or source.get("kind") != "file":
                    raise ValueError("Visual landmark matching requires two original local recordings.")
                sources.append(engine.validate_source(source))
            return lap_detection.start(sources[0]["path"], body.anchor_session_id,
                                       body.model_dump(exclude={"anchor_session_id", "target_session_id"}, exclude_none=True),
                                       target_path=sources[1]["path"], target_session_id=body.target_session_id)

    @app.get("/api/lap-detection/{job_id}")
    def lap_detection_status(job_id: str):
        try:
            return lap_detection.get(job_id)
        except KeyError:
            raise HTTPException(404, "Visual lap search not found.")

    @app.post("/api/lap-detection/{job_id}/cancel")
    def cancel_lap_detection(job_id: str):
        try:
            return lap_detection.cancel(job_id)
        except KeyError:
            raise HTTPException(404, "Visual lap search not found.")

    @app.post("/api/compare")
    def compare(body: ComparisonRequest):
        reference = find_session(body.reference_session_id)
        current = find_session(body.current_session_id)
        same_track = body.confirm_same_track or body.align_start
        comparison_context(current, reference, same_track)
        result = compare_session_ranges(
            engine.store.get_samples(body.reference_session_id), engine.store.get_samples(body.current_session_id),
            reference_range=body.reference_range, current_range=body.current_range,
            axis=body.axis, align_start=body.align_start, max_points=body.max_points,
            alignment_mode=body.alignment_mode, align_end=body.align_end, landmarks=body.landmarks)
        result["sessions"] = {"reference": reference, "current": current}
        return enrich_comparison(result, current, reference, same_track)

    @app.post("/api/trace")
    def trace(body: TraceRequest):
        current = find_session(body.session_id)
        result = trace_session_range(engine.store.get_samples(body.session_id), selection=body.range,
                                     axis=body.axis, max_points=body.max_points)
        result["sessions"] = {"reference": None, "current": current}
        return result

    @app.patch("/api/sessions/{session_id}/reviewed-laps")
    def save_reviewed_lap(session_id: str, body: ReviewedLapRequest):
        session = find_session(session_id)
        if session["status"] == "recording":
            raise ValueError("Finish recording before saving a lap range.")
        lap = body.lap.model_dump(exclude_none=True)
        samples = engine.store.get_samples(session_id)
        selection = {key: lap[key] for key in ("start_time", "end_time", "segment_id") if key in lap}
        trace = trace_session_range(samples, selection=selection, axis="elapsed", max_points=10)["current"]
        if any(abs(trace["selection"][key] - lap[key]) > 1e-6 for key in ("start_time", "end_time")):
            raise ValueError("Saved lap boundaries must lie within the recorded samples.")
        if lap["boundaries_verified"]:
            if not lap.get("start_marker") or not lap.get("end_marker"):
                raise ValueError("Verified boundaries require explicit physical start and finish marker IDs.")
            if trace["has_gaps"]:
                raise ValueError("A verified lap cannot cross a recording discontinuity.")
        elif lap.get("start_marker") or lap.get("end_marker") or lap["landmarks"]:
            raise ValueError("Unverified ranges cannot assert physical marker alignment.")
        markers = lap["landmarks"]
        if (len({item["marker_id"] for item in markers}) != len(markers) or
                any(not lap["start_time"] < item["time"] < lap["end_time"] for item in markers) or
                any(a["time"] >= b["time"] for a, b in zip(markers, markers[1:]))):
            raise ValueError("Track landmarks must be unique, ordered and inside the lap boundaries.")
        return engine.store.save_reviewed_lap(session_id, lap, body.make_default)

    @lru_cache(maxsize=8)
    def saved_frame(path, stamp, width):
        with av.open(path) as container:
            stream = container.streams.video[0]
            base = float(stream.time_base)
            end = float((stream.start_time or 0) + stream.duration) * base if stream.duration else None
            if stamp / base > 2 ** 63 - 1 or (end is not None and stamp > end + .05):
                raise ValueError("Frame time is outside the recording.")
            container.seek(int(stamp / base), stream=stream, backward=True)
            previous = chosen = None
            for frame in container.decode(stream):
                if float(frame.time or 0) >= stamp:
                    chosen = previous if previous is not None and stamp - float(previous.time or 0) <= float(frame.time or 0) - stamp else frame
                    break
                previous = frame
            chosen = chosen if chosen is not None else previous
            if chosen is not None:
                picture = chosen.to_ndarray(format="bgr24")
                if picture.shape[1] > width:
                    picture = cv2.resize(picture, (width, max(1, round(picture.shape[0] * width / picture.shape[1]))),
                                         interpolation=cv2.INTER_AREA)
                ok, jpeg = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if ok:
                    return jpeg.tobytes(), float(chosen.time or 0)
        raise ValueError("No frame at this source time.")

    @app.get("/api/sessions/{session_id}/frame.jpg")
    def session_frame(session_id: str, time: float, width: int = 1280):
        with engine.lifecycle_lock:
            return session_frame_locked(session_id, time, width)

    def session_frame_locked(session_id, time, width):
        if not math.isfinite(time) or time < 0:
            raise ValueError("Frame time must be finite and nonnegative.")
        if not 320 <= width <= 3840:
            raise ValueError("Frame width must be between 320 and 3840 pixels.")
        value = engine.store.get_session(session_id)
        if not value:
            raise HTTPException(404, "Session not found.")
        source = value["source"]
        if not isinstance(source, dict) or source.get("kind") != "file":
            raise HTTPException(404, "This live source has no saved video.")
        try:
            source = engine.validate_source(source)
            jpeg, decoded_time = saved_frame(source["path"], round(time, 6), width)
            return Response(jpeg, media_type="image/jpeg", headers={"X-Source-Time": f"{decoded_time:.6f}"})
        except (ValueError, av.error.FFmpegError):
            raise HTTPException(404, "The original video frame is unavailable.")

    @app.get("/api/export/{session_id}.csv")
    def export(session_id: str):
        find_session(session_id)
        samples = engine.store.get_samples(session_id)
        fields = ["time", "segment_id", "status", "active", "hud_visible", "confidence", "brake", "throttle", "speed", "gear", "steering", "steering_unit", "steering_confidence", "steering_status", "rpm", "lap_time_seconds", "lap_valid", "brake_highlighted", "throttle_highlighted", "distance_m", "acceleration", "processing_ms"]
        labels = {"time": "source_time_s", "brake": "brake_pct", "throttle": "throttle_pct", "speed": "speed_kph", "steering": "steering_hud_pct", "lap_time_seconds": "lap_time_s", "distance_m": "estimated_distance_m", "acceleration": "estimated_longitudinal_accel_m_s2"}
        def rows():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([labels.get(f, f) for f in fields])
            yield output.getvalue()
            for sample in samples:
                output.seek(0)
                output.truncate(0)
                writer.writerow([sample.get(field) for field in fields])
                yield output.getvalue()
        return StreamingResponse(rows(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="accc-{session_id}.csv"'})

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return app


def main():
    parser = argparse.ArgumentParser(description="ACCC Telemetry server")
    parser.add_argument("--host", choices=("127.0.0.1", "::1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"\nACCC Telemetry: http://localhost:{args.port}", flush=True)
    uvicorn.run(create_app(port=args.port), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
