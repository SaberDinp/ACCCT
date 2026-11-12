"""Source-backed ACC identities, separate from calibration and video validation.

An entry means the content exists in ACC, not that a camera, HUD, extraction
profile or downloaded lap has been tested. Never infer a variant from its make.
"""

from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata


CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "catalog" / "acc.json"


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@lru_cache(maxsize=1)
def _data() -> dict:
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    sources = {source["id"] for source in value["sources"]}
    for kind in ("cars", "tracks"):
        seen = set()
        aliases = {}
        for item in value[kind]:
            if item["id"] in seen:
                raise ValueError(f"Duplicate {kind} identity: {item['id']}")
            seen.add(item["id"])
            if not item["source_ids"] or not set(item["source_ids"]) <= sources:
                raise ValueError(f"Missing catalog provenance: {item['id']}")
            item["platforms"] = list(value["platforms"])
            if kind == "cars":
                item["category"] = "GT3"
            for alias in (item["id"], item["name"], *item["aliases"]):
                key = _key(alias)
                if key in aliases and aliases[key] != item["id"]:
                    raise ValueError(f"Ambiguous catalog alias: {alias}")
                aliases[key] = item["id"]
    return value


def _list(kind: str, platform: str | None) -> list[dict]:
    data = _data()
    if platform is not None and platform not in data["platforms"]:
        raise ValueError("Platform must be pc, ps5 or xbox_series")
    return copy.deepcopy([item for item in data[kind]
                          if platform is None or platform in item["platforms"]])


def list_cars(platform: str | None = None) -> list[dict]:
    """List distinct GT3 variants, excluding Cup/Challenge/Super Trofeo cars."""
    return _list("cars", platform)


def list_circuits(platform: str | None = None) -> list[dict]:
    """List unique ACC circuits, without manufacturing per-season layouts."""
    return _list("tracks", platform)


def _get(kind: str, identity: str) -> dict:
    if not isinstance(identity, str):
        raise ValueError("Catalog identity must be a string")
    for item in _data()[kind]:
        if item["id"] == identity:
            return copy.deepcopy(item)
    raise ValueError(f"Unknown ACC {'car' if kind == 'cars' else 'circuit'}: {identity}")


def get_car(car_id: str) -> dict:
    return _get("cars", car_id)


def get_circuit(track_id: str) -> dict:
    return _get("tracks", track_id)


def lap_search_defaults(track_id: str | None = None) -> dict[str, float]:
    """Bound visual work without treating one circuit's lap time as universal.

    These are adjustable search limits, not pace predictions or lap validity
    rules. An unknown circuit must also allow the combined Nurburgring layout.
    """
    if track_id is not None:
        get_circuit(track_id)
    return {"min_lap_seconds": 30.0,
            "max_lap_seconds": 900.0 if track_id in {None, "nurburgring_24h"} else 300.0}


@lru_cache(maxsize=2)
def _aliases(kind: str) -> dict[str, str]:
    return {_key(alias): item["id"] for item in _data()[kind]
            for alias in (item["id"], item["name"], *item["aliases"])}


def _resolve(kind: str, value: str | None) -> dict | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Catalog alias must be a string")
    identity = _aliases(kind).get(_key(value))
    return _get(kind, identity) if identity else None


def resolve_car(value: str | None) -> dict | None:
    """Resolve an exact id/name/alias, never a fuzzy title or guessed variant."""
    return _resolve("cars", value)


def resolve_circuit(value: str | None) -> dict | None:
    return _resolve("tracks", value)


def catalog() -> dict:
    """Return a detached, JSON-compatible roster including source provenance."""
    result = copy.deepcopy(_data())
    result["counts"] = {"gt3_cars": len(result["cars"]), "circuits": len(result["tracks"]),
                        "car_track_pairs": len(result["cars"]) * len(result["tracks"])}
    return result
