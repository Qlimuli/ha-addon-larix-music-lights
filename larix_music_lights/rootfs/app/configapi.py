#!/usr/bin/env python3
"""Larix Music Reactive Lights - Config API v1.6.1"""
import json, logging, os, time
from typing import Any, Dict, List, Optional
import requests

log = logging.getLogger("larix-music.configapi")
SUPERVISOR_URL = "http://supervisor"
CORE_API = f"{SUPERVISOR_URL}/core/api"
OPTIONS_PATH = "/data/options.json"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})

GLOBAL_KEYS = [
    "enabled","active_profile","mode","sensitivity","update_interval_ms","transition",
    "min_brightness","max_brightness","color_mode","base_hue","beat_threshold",
    "silence_timeout_s","rtmp_app","rtmp_stream","log_level",
    "light_entities","area_ids","bass_lights","mid_lights","high_lights","full_lights",
]
PROFILE_KEYS = [
    "name","room","area_ids","mode","sensitivity","min_brightness","max_brightness",
    "transition","beat_threshold","base_hue","bass_lights","mid_lights","high_lights","full_lights",
]

class ConfigApiError(Exception):
    pass

def read_current_options() -> Dict[str, Any]:
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("read options: %s", e)
        return {}

def _ha_get(path: str) -> Optional[Any]:
    if not TOKEN:
        return None
    try:
        r = _session.get(f"{CORE_API}{path}", timeout=8)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json() if r.content else None
    except Exception as e:
        log.debug("HA GET %s: %s", path, e)
        return None

def list_light_entities() -> List[Dict[str, Any]]:
    states = _ha_get("/states") or []
    lights = []
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith("light."):
            continue
        attrs = s.get("attributes", {}) or {}
        lights.append({
            "entity_id": eid,
            "name": attrs.get("friendly_name", eid),
            "area_id": attrs.get("area_id") or attrs.get("area") or "",
        })
    lights.sort(key=lambda x: x["name"].lower())
    return lights

def list_areas() -> List[Dict[str, str]]:
    areas = _ha_get("/config/area_registry/list")
    if isinstance(areas, list) and areas:
        return [{"area_id": a.get("area_id",""), "name": a.get("name", a.get("area_id",""))} for a in areas]
    return []

def _sanitize_profile(p: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}
    for key in PROFILE_KEYS:
        if key in p and p[key] not in (None, ""):
            clean[key] = p[key]
    clean.setdefault("name", "")
    for list_key in ("area_ids","bass_lights","mid_lights","high_lights","full_lights"):
        clean.setdefault(list_key, [])
    return clean

def sanitize_options(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigApiError("Payload must be a JSON object")
    out = read_current_options() or {}
    for key in GLOBAL_KEYS:
        if key in raw:
            out[key] = raw[key]
    if "update_interval_ms" in out:
        try:
            out["update_interval_ms"] = max(120, min(500, int(out["update_interval_ms"])))
        except Exception:
            out["update_interval_ms"] = 180
    if "profiles" in raw:
        profiles = raw.get("profiles", [])
        if not isinstance(profiles, list):
            raise ConfigApiError("profiles must be a list")
        out["profiles"] = [_sanitize_profile(p) for p in profiles if isinstance(p, dict)]
    for k in ("light_entities","area_ids","bass_lights","mid_lights","high_lights","full_lights"):
        out.setdefault(k, [])
        if not isinstance(out[k], list):
            out[k] = []
    if not out.get("full_lights") and out.get("light_entities"):
        out["full_lights"] = list(out["light_entities"])
    if out.get("full_lights") and not out.get("light_entities"):
        out["light_entities"] = list(out["full_lights"])
    return out

def save_options(options: Dict[str, Any]) -> None:
    try:
        with open(OPTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(options, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        log.info("Wrote %s", OPTIONS_PATH)
    except Exception as e:
        log.error("write options file: %s", e)
        raise ConfigApiError(f"Cannot write options: {e}") from e
    if TOKEN:
        try:
            r = _session.post(f"{SUPERVISOR_URL}/addons/self/options",
                              json={"options": options}, timeout=15)
            r.raise_for_status()
        except Exception as e:
            log.warning("Supervisor options API: %s (file already written)", e)

def restart_addon() -> None:
    if not TOKEN:
        return
    try:
        _session.post(f"{SUPERVISOR_URL}/addons/self/restart", timeout=30)
    except Exception as e:
        log.warning("restart: %s", e)

def touch_reload_flag() -> None:
    try:
        with open("/tmp/larix_reload", "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        log.warning("reload flag: %s", e)
