#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Audio analyzer & light controller (v1.4.0)
"""

import os
import sys
import json
import time
import logging
import subprocess
import threading
import signal
import colorsys
from collections import deque
from typing import List, Optional, Dict, Any

import numpy as np
import requests

try:
    import webui
    from watchdog_state import WatchdogState
    _HAS_WEBUI = True
except Exception as _e:
    webui = None
    WatchdogState = None
    _HAS_WEBUI = False
    print(f"WARNING: webui not available: {_e}", flush=True)

ENABLED = os.getenv("ADDON_ENABLED", "true").lower() == "true"
ACTIVE_PROFILE = os.getenv("ADDON_ACTIVE_PROFILE", "").strip()
GLOBAL_MODE = os.getenv("ADDON_MODE", "auto")
GLOBAL_SENSITIVITY = float(os.getenv("ADDON_SENSITIVITY", "1.0"))
UPDATE_MS = int(os.getenv("ADDON_UPDATE_MS", "80"))
GLOBAL_TRANSITION = float(os.getenv("ADDON_TRANSITION", "0.15"))
GLOBAL_MIN_BRIGHT = int(os.getenv("ADDON_MIN_BRIGHT", "20"))
GLOBAL_MAX_BRIGHT = int(os.getenv("ADDON_MAX_BRIGHT", "255"))
COLOR_MODE = os.getenv("ADDON_COLOR_MODE", "spectrum")
GLOBAL_BASE_HUE = int(os.getenv("ADDON_BASE_HUE", "0"))
GLOBAL_BEAT_THRESH = float(os.getenv("ADDON_BEAT_THRESH", "0.55"))
SILENCE_S = int(os.getenv("ADDON_SILENCE_S", "8"))
RTMP_APP = os.getenv("ADDON_RTMP_APP", "live")
RTMP_STREAM = os.getenv("ADDON_RTMP_STREAM", "music")
LOG_LEVEL = os.getenv("ADDON_LOG_LEVEL", "info").upper()
WEBUI_PORT = int(os.getenv("ADDON_WEBUI_PORT", "8099"))


def _load_json_env(key: str, default):
    raw = os.getenv(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _load_options_file() -> Dict[str, Any]:
    for path in (
        os.getenv("ADDON_OPTIONS_FILE", ""),
        "/data/options.json",
        "/config/options.json",
    ):
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                print(f"WARNING: Could not read options file {path}: {e}", flush=True)
    return {}


_OPTIONS = _load_options_file()

_raw_lights = _OPTIONS.get("light_entities")
if _raw_lights is None:
    _raw_lights = _load_json_env("ADDON_LIGHT_ENTITIES", [])
LEGACY_LIGHTS: List[str] = [str(x) for x in _raw_lights] if isinstance(_raw_lights, list) else []

_raw_areas = _OPTIONS.get("area_ids")
if _raw_areas is None:
    _raw_areas = _load_json_env("ADDON_AREA_IDS", [])
LEGACY_AREAS: List[str] = [str(x) for x in _raw_areas] if isinstance(_raw_areas, list) else []

_raw_profiles = _OPTIONS.get("profiles")
if _raw_profiles is None:
    _raw_profiles = _load_json_env("ADDON_PROFILES", [])
if isinstance(_raw_profiles, list):
    PROFILES: List[Dict[str, Any]] = [p for p in _raw_profiles if isinstance(p, dict)]
else:
    PROFILES = []

if "mode" in _OPTIONS and _OPTIONS["mode"]:
    GLOBAL_MODE = str(_OPTIONS["mode"])
if "sensitivity" in _OPTIONS and _OPTIONS["sensitivity"] is not None:
    GLOBAL_SENSITIVITY = float(_OPTIONS["sensitivity"])
if "update_interval_ms" in _OPTIONS and _OPTIONS["update_interval_ms"] is not None:
    UPDATE_MS = int(_OPTIONS["update_interval_ms"])
if "transition" in _OPTIONS and _OPTIONS["transition"] is not None:
    GLOBAL_TRANSITION = float(_OPTIONS["transition"])
if "min_brightness" in _OPTIONS and _OPTIONS["min_brightness"] is not None:
    GLOBAL_MIN_BRIGHT = int(_OPTIONS["min_brightness"])
if "max_brightness" in _OPTIONS and _OPTIONS["max_brightness"] is not None:
    GLOBAL_MAX_BRIGHT = int(_OPTIONS["max_brightness"])
if "beat_threshold" in _OPTIONS and _OPTIONS["beat_threshold"] is not None:
    GLOBAL_BEAT_THRESH = float(_OPTIONS["beat_threshold"])
if "base_hue" in _OPTIONS and _OPTIONS["base_hue"] is not None:
    GLOBAL_BASE_HUE = int(_OPTIONS["base_hue"])
if "silence_timeout_s" in _OPTIONS and _OPTIONS["silence_timeout_s"] is not None:
    SILENCE_S = int(_OPTIONS["silence_timeout_s"])
if "rtmp_app" in _OPTIONS and _OPTIONS["rtmp_app"]:
    RTMP_APP = str(_OPTIONS["rtmp_app"])
if "rtmp_stream" in _OPTIONS and _OPTIONS["rtmp_stream"]:
    RTMP_STREAM = str(_OPTIONS["rtmp_stream"])
if "enabled" in _OPTIONS:
    ENABLED = bool(_OPTIONS["enabled"])
if "active_profile" in _OPTIONS and _OPTIONS["active_profile"] is not None:
    ACTIVE_PROFILE = str(_OPTIONS["active_profile"]).strip()
if "log_level" in _OPTIONS and _OPTIONS["log_level"]:
    LOG_LEVEL = str(_OPTIONS["log_level"]).upper()

HA_URL = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")

SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_SAMPLES = 2048
BYTES_PER_CHUNK = CHUNK_SAMPLES * SAMPLE_WIDTH * CHANNELS

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("larix-music")
log.info("Config loaded: lights=%s profiles=%d", LEGACY_LIGHTS, len(PROFILES))

state: Optional[Any] = None


def load_active_profile() -> Dict[str, Any]:
    profiles = PROFILES if isinstance(PROFILES, list) else []
    if profiles:
        if ACTIVE_PROFILE:
            for p in profiles:
                if isinstance(p, dict) and p.get("name") == ACTIVE_PROFILE:
                    return p
        p = profiles[0] if isinstance(profiles[0], dict) else {}
        return p
    return {
        "name": "legacy",
        "room": "",
        "area_ids": LEGACY_AREAS,
        "mode": GLOBAL_MODE,
        "sensitivity": GLOBAL_SENSITIVITY,
        "min_brightness": GLOBAL_MIN_BRIGHT,
        "max_brightness": GLOBAL_MAX_BRIGHT,
        "transition": GLOBAL_TRANSITION,
        "beat_threshold": GLOBAL_BEAT_THRESH,
        "base_hue": GLOBAL_BASE_HUE,
        "bass_lights": [],
        "mid_lights": [],
        "high_lights": [],
        "full_lights": LEGACY_LIGHTS,
    }


def profile_value(profile: Dict[str, Any], key: str, default):
    v = profile.get(key)
    return default if v is None else v


class HomeAssistant:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        )
        self._area_lights_cache: Dict[str, List[str]] = {}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{HA_URL}{path}"
        try:
            r = self.session.request(method, url, timeout=5, **kwargs)
            r.raise_for_status()
            if r.content:
                return r.json()
            return True
        except Exception as e:
            log.warning("HA API error %s %s: %s", method, path, e)
            return None

    def lights_for_areas(self, area_ids: List[str]) -> List[str]:
        if not area_ids:
            return []
        key = ",".join(sorted(area_ids))
        if key in self._area_lights_cache:
            return self._area_lights_cache[key]
        entities = set()
        states = self._request("GET", "/states") or []
        if isinstance(states, list):
            for s in states:
                eid = s.get("entity_id", "")
                if not eid.startswith("light."):
                    continue
                attrs = s.get("attributes", {})
                area = attrs.get("area_id") or attrs.get("area")
                if area in area_ids:
                    entities.add(eid)
        result = sorted(entities)
        self._area_lights_cache[key] = result
        return result

    def set_lights(
        self,
        entity_ids: List[str],
        brightness: int,
        hs_color: Optional[tuple] = None,
        transition: float = GLOBAL_TRANSITION,
    ):
        if not entity_ids:
            return
        brightness = max(1, min(255, int(brightness)))
        payload: Dict[str, Any] = {
            "entity_id": entity_ids,
            "brightness": brightness,
            "transition": max(0.0, float(transition)),
        }
        if hs_color is not None:
            try:
                h = (float(hs_color[0]) % 360) / 360.0
                s = max(0.0, min(1.0, float(hs_color[1]) / 100.0))
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                r2 = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
                g2 = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
                b2 = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92
                X = r2 * 0.4124 + g2 * 0.3576 + b2 * 0.1805
                Y = r2 * 0.2126 + g2 * 0.7152 + b2 * 0.0722
                Z = r2 * 0.0193 + g2 * 0.1192 + b2 * 0.9505
                tot = X + Y + Z
                if tot > 1e-6:
                    payload["xy_color"] = [round(X / tot, 4), round(Y / tot, 4)]
            except Exception:
                payload["hs_color"] = [float(hs_color[0]) % 360, float(hs_color[1])]
        result = self._request("POST", "/services/light/turn_on", json=payload)
        if result is None:
            self._request(
                "POST",
                "/services/light/turn_on",
                json={
                    "entity_id": entity_ids,
                    "brightness": brightness,
                    "transition": max(0.0, float(transition)),
                },
            )

    def all_off(self, entity_ids: List[str], transition: float = 1.5):
        if entity_ids:
            self._request(
                "POST",
                "/services/light/turn_off",
                json={"entity_id": entity_ids, "transition": transition},
            )


class AudioAnalyzer:
    def __init__(self, beat_threshold: float, base_hue: float):
        self.bass_hist = deque(maxlen=30)
        self.energy_hist = deque(maxlen=20)
        self.last_beat = 0.0
        self.hue = float(base_hue)
        self.beat_threshold = beat_threshold
        self.silence_start: Optional[float] = None
        self.running = True

    def process(self, pcm: bytes, sensitivity: float) -> Dict[str, float]:
        if len(pcm) < 1024:
            return {"amplitude": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "beat": 0.0}
        if len(pcm) % 2:
            pcm = pcm[:-1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if samples.size == 0:
            return {"amplitude": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "beat": 0.0}
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        samples /= 32768.0
        window = np.hanning(len(samples))
        samples = samples * window
        fft = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)
        bass = self._band_energy(fft, freqs, 20, 150)
        mid = self._band_energy(fft, freqs, 150, 2000)
        high = self._band_energy(fft, freqs, 2000, 8000)
        amplitude = float(np.sqrt(np.mean(samples ** 2)))
        bass = min(1.0, bass * 10.0 * sensitivity)
        mid = min(1.0, mid * 9.0 * sensitivity)
        high = min(1.0, high * 8.0 * sensitivity)
        gain = 10.0 if peak < 2000 else 6.0
        amplitude = min(1.0, amplitude * gain * sensitivity)
        self.bass_hist.append(bass)
        self.energy_hist.append(amplitude)
        beat = 0.0
        now = time.time()
        if len(self.bass_hist) >= 5:
            avg = sum(self.bass_hist) / len(self.bass_hist)
            if bass > avg * (1.0 + self.beat_threshold) and (now - self.last_beat) > 0.25:
                beat = 1.0
                self.last_beat = now
        return {"amplitude": amplitude, "bass": bass, "mid": mid, "high": high, "beat": beat}

    @staticmethod
    def _band_energy(fft, freqs, f_low, f_high):
        mask = (freqs >= f_low) & (freqs < f_high)
        if not np.any(mask):
            return 0.0
        return float(np.mean(fft[mask]))


def resolve_band_lights(ha: HomeAssistant, profile: Dict[str, Any]) -> Dict[str, List[str]]:
    area_ids = profile.get("area_ids") or []
    area_lights = ha.lights_for_areas(area_ids) if area_ids else []
    bass = list(profile.get("bass_lights") or [])
    mid = list(profile.get("mid_lights") or [])
    high = list(profile.get("high_lights") or [])
    full = list(profile.get("full_lights") or [])
    if not any([bass, mid, high, full]):
        full = area_lights or list(LEGACY_LIGHTS)
    if not full and area_lights:
        full = area_lights
    log.info("Band lights - full: %s", full or "(none)")
    return {"bass": bass, "mid": mid, "high": high, "full": full}


def map_to_lights(
    ha, analyzer, features, bands, mode, min_b, max_b, transition
):
    amp = features["amplitude"]
    bass = features["bass"]
    mid = features["mid"]
    high = features["high"]
    beat = features["beat"]
    if amp < 0.005:
        if analyzer.silence_start is None:
            analyzer.silence_start = time.time()
        return
    analyzer.silence_start = None

    def bright(val: float) -> int:
        return max(min_b, min(max_b, int(min_b + (max_b - min_b) * val)))

    if mode in ("auto", "automatic"):
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        total = bass + mid + high + 1e-6
        hue = (bass / total * 10 + mid / total * 140 + high / total * 250) % 360
        sat = 55 + min(40, (bass + high) * 25)
        if beat > 0.5:
            ha.set_lights(targets, max_b, hs_color=(hue, min(95, sat + 20)), transition=0.05)
            analyzer.hue = hue
        else:
            level = max(0.18, amp * 0.85 + bass * 0.25)
            ha.set_lights(targets, bright(level), hs_color=(hue, sat), transition=transition)
            analyzer.hue = hue

    elif mode == "pulse":
        targets = bands["bass"] or bands["full"] or bands["mid"] or bands["high"]
        if beat > 0.5:
            ha.set_lights(targets, max_b, hs_color=(analyzer.hue % 360, 90), transition=0.05)
            analyzer.hue += 30
        else:
            ha.set_lights(targets, bright(max(0.15, amp)), transition=transition)

    elif mode == "spectrum":
        if bands["bass"]:
            ha.set_lights(bands["bass"], bright(bass), hs_color=(0, 80), transition=transition)
        if bands["mid"]:
            ha.set_lights(bands["mid"], bright(mid), hs_color=(120, 70), transition=transition)
        if bands["high"]:
            ha.set_lights(bands["high"], bright(high), hs_color=(240, 70), transition=transition)
        if bands["full"]:
            hue = (bass * 0 + mid * 120 + high * 240) % 360
            ha.set_lights(bands["full"], bright(amp), hs_color=(hue, 60), transition=transition)

    elif mode == "color_cycle":
        analyzer.hue = (analyzer.hue + 2 + bass * 8) % 360
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(max(0.2, amp)), hs_color=(analyzer.hue, 80), transition=transition)

    elif mode == "brightness":
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(max(0.15, amp)), transition=transition)

    elif mode == "cinema":
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(0.3 + bass * 0.45), hs_color=(30, 70), transition=0.3)

    else:
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(max(0.15, amp)), transition=transition)


def start_ffmpeg() -> subprocess.Popen:
    rtmp_url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-listen", "1", "-timeout", "0", "-i", rtmp_url,
        "-vn", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    log.info("Starting FFmpeg RTMP listener: %s", rtmp_url)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)


def read_stderr(proc: subprocess.Popen):
    if proc.stderr is None:
        return
    for line in iter(proc.stderr.readline, b""):
        if line:
            text = line.decode(errors="replace").rstrip()
            if not text or text.startswith("size="):
                continue
            low = text.lower()
            if any(k in low for k in ("error", "fail", "invalid", "unable", "not found")):
                log.warning("ffmpeg: %s", text)
            else:
                log.info("ffmpeg: %s", text)


class _NullState:
    def set(self, **kwargs): pass
    def log_event(self, *a, **k): pass
    def mark_error(self, *a, **k): pass
    def mark_stopped(self, *a, **k): pass
    def mark_waiting(self, *a, **k): pass
    def mark_no_signal(self, *a, **k): pass
    def mark_chunk_received(self, *a, **k): pass
    def mark_light_update(self, *a, **k): pass
    def mark_ffmpeg_restart(self, *a, **k): pass
    def set_features(self, *a, **k): pass


def main():
    global state
    if _HAS_WEBUI:
        state = WatchdogState()
        state.set(enabled=ENABLED, rtmp_url=f"rtmp://<HA-IP>:1935/{RTMP_APP}/{RTMP_STREAM}")
        webui.start_server(state, port=WEBUI_PORT)
    else:
        state = _NullState()

    if not TOKEN:
        log.error("SUPERVISOR_TOKEN missing")
        sys.exit(1)
    if not ENABLED:
        log.info("Add-on disabled")
        while True:
            time.sleep(1)

    profile = load_active_profile()
    mode = profile_value(profile, "mode", GLOBAL_MODE)
    sensitivity = float(profile_value(profile, "sensitivity", GLOBAL_SENSITIVITY))
    min_b = int(profile_value(profile, "min_brightness", GLOBAL_MIN_BRIGHT))
    max_b = int(profile_value(profile, "max_brightness", GLOBAL_MAX_BRIGHT))
    transition = float(profile_value(profile, "transition", GLOBAL_TRANSITION))
    beat_thresh = float(profile_value(profile, "beat_threshold", GLOBAL_BEAT_THRESH))
    base_hue = float(profile_value(profile, "base_hue", GLOBAL_BASE_HUE))

    ha = HomeAssistant()
    bands = resolve_band_lights(ha, profile)
    analyzer = AudioAnalyzer(beat_threshold=beat_thresh, base_hue=base_hue)
    all_entities = list(set(bands["bass"] + bands["mid"] + bands["high"] + bands["full"]))

    state.set(profile_name=profile.get("name", ""), room=profile.get("room", ""),
              mode=mode, sensitivity=sensitivity, bands=bands, connection_state="waiting")

    def shutdown(signum, frame):
        analyzer.running = False
        ha.all_off(all_entities)
        state.mark_stopped()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("Active: mode=%s sensitivity=%.2f lights=%s", mode, sensitivity, all_entities)

    while analyzer.running:
        proc = start_ffmpeg()
        threading.Thread(target=read_stderr, args=(proc,), daemon=True).start()
        log.info("Waiting for Larix Broadcaster to connect...")
        state.mark_waiting()
        last_update = 0.0
        last_data_at = time.time()
        last_log_sec = -1
        pcm_buf = bytearray()
        try:
            while analyzer.running and proc.poll() is None:
                chunk = proc.stdout.read(BYTES_PER_CHUNK)
                if not chunk:
                    if time.time() - last_data_at > 4.0:
                        state.mark_no_signal()
                    time.sleep(0.02)
                    continue
                last_data_at = time.time()
                state.mark_chunk_received()
                pcm_buf.extend(chunk)
                while len(pcm_buf) >= BYTES_PER_CHUNK:
                    frame = bytes(pcm_buf[:BYTES_PER_CHUNK])
                    del pcm_buf[:BYTES_PER_CHUNK]
                    features = analyzer.process(frame, sensitivity)
                    state.set_features(features)
                    now = time.time()
                    if (now - last_update) * 1000 >= UPDATE_MS:
                        map_to_lights(ha, analyzer, features, bands, mode, min_b, max_b, transition)
                        state.mark_light_update()
                        sec = int(now)
                        if sec != last_log_sec and sec % 2 == 0:
                            peak = 0
                            try:
                                s = np.frombuffer(frame, dtype=np.int16)
                                peak = int(np.max(np.abs(s))) if len(s) else 0
                            except Exception:
                                pass
                            log.info(
                                "audio amp=%.3f bass=%.3f mid=%.3f high=%.3f beat=%.0f peak=%d mode=%s",
                                features["amplitude"], features["bass"], features["mid"],
                                features["high"], features["beat"], peak, mode,
                            )
                            last_log_sec = sec
                        last_update = now
        except Exception as e:
            log.error("Main loop error: %s", e)
            state.mark_error(str(e))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if analyzer.running:
            log.warning("FFmpeg exited - restarting in 3 s...")
            state.mark_ffmpeg_restart()
            time.sleep(3)


if __name__ == "__main__":
    main()
