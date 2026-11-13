"""Curated recording choices and durable, on-demand reference import reuse."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlparse

from .catalog import catalog, get_car, get_circuit
from .imports import TERMINAL

CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets/references/catalog.json"


@lru_cache(maxsize=1)
def reference_catalog():
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("references"), list):
        raise ValueError("The reference library catalog is invalid.")
    ids, urls = set(), set()
    for entry in value["references"]:
        identifier, url = entry.get("id"), entry.get("url")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,159}", identifier):
            raise ValueError("The reference library contains an invalid identifier.")
        parsed = urlparse(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme != "https" or parsed.netloc != "www.youtube.com"
                or parsed.path != "/watch" or parsed.fragment
                or set(parse_qs(parsed.query)) != {"v"}
                or not re.fullmatch(r"[A-Za-z0-9_-]{11}", parse_qs(parsed.query).get("v", [""])[0])):
            raise ValueError("The reference library contains an invalid public video URL.")
        if identifier in ids or url in urls:
            raise ValueError("The reference library contains a duplicate recording.")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            raise ValueError("The reference library contains an unnamed recording.")
        get_car(entry["car_id"])
        get_circuit(entry["track_id"])
        ids.add(identifier)
        urls.add(url)
    return value


class ReferenceLibrary:
    def __init__(self, engine, imports):
        self.engine, self.imports = engine, imports
        self.lock = threading.RLock()

    @staticmethod
    def _entry(identifier):
        for entry in reference_catalog()["references"]:
            if entry["id"] == identifier:
                return copy.deepcopy(entry)
        raise KeyError(identifier)

    def _ready(self, entry, sessions):
        for session in reversed(sessions):
            source = session["source"]
            if (session["status"] != "completed" or not isinstance(source, dict)
                    or source.get("reference_library_id") != entry["id"]
                    or source.get("original_url") != entry["url"]):
                continue
            try:
                handle, _, _ = self.engine.open_video_media(session["id"])
                handle.close()
            except (KeyError, ValueError, OSError, RuntimeError):
                continue
            return session["id"]
        return None

    @staticmethod
    def _active(entry, jobs):
        return next((job for job in jobs if job["state"] not in TERMINAL
                     and job.get("source", {}).get("curated_reference", {}).get("id") == entry["id"]), None)

    def list(self, car_id=None, track_id=None):
        if car_id is not None:
            get_car(car_id)
        if track_id is not None:
            get_circuit(track_id)
        with self.engine.lifecycle_lock, self.lock:
            data = reference_catalog()
            entries = [copy.deepcopy(entry) for entry in data["references"]
                       if (car_id is None or entry["car_id"] == car_id)
                       and (track_id is None or entry["track_id"] == track_id)]
            sessions, jobs = self.engine.store.list_session_sources(), self.imports.list()
            for entry in entries:
                session_id, job = self._ready(entry, sessions), self._active(entry, jobs)
                entry["cache"] = {"state": "ready" if session_id else "importing" if job else "not_cached",
                                  "session_id": session_id, "job_id": job["id"] if job else None}
            roster = catalog()
            return {"references": entries, "count": len(entries), "updated_at": data.get("updated_at"),
                    "cars": roster["cars"], "tracks": roster["tracks"]}

    def start(self, identifier):
        # The lifecycle lock also protects deletion. Keep lookup and job creation
        # together so simultaneous clicks cannot start duplicate downloads.
        with self.engine.lifecycle_lock, self.lock:
            entry = self._entry(identifier)
            session_id = self._ready(entry, self.engine.store.list_session_sources())
            if session_id:
                return {"reference_id": identifier, "state": "ready", "session_id": session_id,
                        "job": None, "reused": True}
            job = self._active(entry, self.imports.list())
            reused = job is not None
            if job is None:
                context = {"car_id": entry["car_id"], "track_id": entry["track_id"]}
                if entry.get("game_version"):
                    context["game_version"] = entry["game_version"]
                job = self.imports.start({"kind": "youtube", "url": entry["url"], "name": entry["title"][:180],
                                          "role": "reference", "context": context,
                                          "curated_reference": entry, "auto_calibrate": True})
            return {"reference_id": identifier, "state": "importing", "session_id": job.get("session_id"),
                    "job": job, "reused": reused}
