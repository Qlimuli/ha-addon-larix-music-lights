#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Config API
Thin wrapper around the Home Assistant Supervisor API used by the Settings
tab of the Ingress web UI, so the full add-on configuration (mode,
thresholds, light selection, room profiles, ...) can be edited without
touching YAML.

Reading options is done directly from /data/options.json (always present
and always up to date, no extra permissions required). Writing options and
restarting the add-on go through the Supervisor API and require
`hassio_api: true` + `hassio_role: manager` in config.yaml.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("larix-music.configapi")

SUPERVISOR_URL = "http://supervisor"
CORE_API = f"{SUPERVISOR_URL}/core/api"
OPTIONS_PATH = "/data/options.json"

TOKEN = os.getenv("SUPERVISOR_TOKEN", "")

_session = requests.Session()
_session.headers.update(
    {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
)

GLOBAL_KEYS = [
    "enabled",
    "active_profile",
    "mode",
    "sensitivity",
    "update_interval_ms",
    "transition",
    "min_brightness",
    "max_brightness",
    "color_mode",
    "base_hue",
    "beat_threshold",
    "silence_timeout_s",
    "rtmp_app",
    "rtmp_stream",
    "log_level",
    "light_entities",
    "area_ids",
    "bass_lights",
    "mid_lights",
    "high_lights",
    "full_lights",
]

PROFILE_KEYS = [
    "name",
    "room",
    "area_ids",
    "mode",
    "sensitivity",
    "min_brightness",
    "max_brightness",
    "transition",
    "beat_threshold",
    "base_hue",
    "bass_lights",
    "mid_lights",
    "high_lights",
    "full_lights",
]


class ConfigApiError(Exception):
    pass


def read_current_options() -> Dict[str, Any]:
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Could not read %s: %s", OPTIONS_PATH, e)
        return {}


def _ha_get(path: str) -> Optional[Any]:
    if not TOKEN:
        log.warning("SUPERVISOR_TOKEN missing - cannot query Home Assistant")
        return None
    try:
        r = _session.get(f"{CORE_API}{path}", timeout=10)
        r.raise_for_status()
        if r.content:
            return r.json()
        return None
    except Exception as e:
        log.warning("HA API error GET %s: %s", path, e)
        return None


def list_light_entities() -> List[Dict[str, Any]]:
    states = _ha_get("/states") or []
    registry = _ha_get("/config/entity_registry/list") or []
    area_by_entity: Dict[str, str] = {}
    for e in registry:
        eid = e.get("entity_id", "")
        if eid.startswith("light."):
            area = e.get("area_id")
            if area:
                area_by_entity[eid] = area

    lights = []
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith("light."):
            continue
        attrs = s.get("attributes", {}) or {}
        lights.append(
            {
                "entity_id": eid,
                "name": attrs.get("friendly_name", eid),
                "area_id": area_by_entity.get(eid) or attrs.get("area_id") or attrs.get("area") or "",
            }
        )
    lights.sort(key=lambda x: x["name"].lower())
    return lights


def list_areas() -> List[Dict[str, str]]:
    areas = _ha_get("/config/area_registry/list")
    if areas:
        return [{"area_id": a.get("area_id", ""), "name": a.get("name", a.get("area_id", ""))} for a in areas]

    seen = {}
    for light in list_light_entities():
        aid = light.get("area_id")
        if aid and aid not in seen:
            seen[aid] = aid
    return [{"area_id": k, "name": v} for k, v in sorted(seen.items())]


def _sanitize_profile(p: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}
    for key in PROFILE_KEYS:
        if key in p and p[key] not in (None, ""):
            clean[key] = p[key]
    clean.setdefault("name", "")
    for list_key in ("area_ids", "bass_lights", "mid_lights", "high_lights", "full_lights"):
        clean.setdefault(list_key, [])
    return clean


def sanitize_options(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigApiError("Payload must be a JSON object")

    out: Dict[str, Any] = {}
    for key in GLOBAL_KEYS:
        if key in raw:
            out[key] = raw[key]

    profiles = raw.get("profiles", [])
    if not isinstance(profiles, list):
        raise ConfigApiError("profiles must be a list")
    out["profiles"] = [_sanitize_profile(p) for p in profiles if isinstance(p, dict)]

    out.setdefault("light_entities", [])
    out.setdefault("area_ids", [])
    out.setdefault("bass_lights", [])
    out.setdefault("mid_lights", [])
    out.setdefault("high_lights", [])
    out.setdefault("full_lights", [])

    return out


def save_options(options: Dict[str, Any]) -> None:
    if not TOKEN:
        raise ConfigApiError("SUPERVISOR_TOKEN missing - cannot save options")
    try:
        r = _session.post(f"{SUPERVISOR_URL}/addons/self/options", json={"options": options}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error("Failed to save options via Supervisor API: %s", e)
        raise ConfigApiError(f"Supervisor rejected the new options: {e}") from e


def restart_addon() -> None:
    if not TOKEN:
        raise ConfigApiError("SUPERVISOR_TOKEN missing - cannot restart add-on")
    try:
        r = _session.post(f"{SUPERVISOR_URL}/addons/self/restart", timeout=30)
        r.raise_for_status()
    except Exception as e:
        log.warning("Restart request returned an error (may still be applying): %s", e)


def touch_reload_flag() -> None:
    """Signal the analyzer loop to hot-reload options without process restart."""
    try:
        with open("/tmp/larix_reload", "w", encoding="utf-8") as f:
            f.write("1")
    except Exception as e:
        log.warning("Could not write reload flag: %s", e)
