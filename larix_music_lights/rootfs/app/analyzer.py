#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Audio analyzer & light controller (v1.1)
Receives RTMP stream from Larix Broadcaster, performs real-time FFT,
and drives Home Assistant lights with band-specific assignments and room profiles.
"""

import os
import sys
import json
import time
import logging
import subprocess
import threading
import signal
from collections import deque
from typing import List, Optional, Dict, Any

import numpy as np
import requests

import webui
from watchdog_state import WatchdogState

# ---------------------------------------------------------------------------
# Configuration from environment (set by run.sh / bashio)
# ---------------------------------------------------------------------------
ENABLED = os.getenv("ADDON_ENABLED", "true").lower() == "true"
ACTIVE_PROFILE = os.getenv("ADDON_ACTIVE_PROFILE", "").strip()
GLOBAL_MODE = os.getenv("ADDON_MODE", "spectrum")
GLOBAL_SENSITIVITY = float(os.getenv("ADDON_SENSITIVITY", "0.7"))
UPDATE_MS = int(os.getenv("ADDON_UPDATE_MS", "80"))
GLOBAL_TRANSITION = float(os.getenv("ADDON_TRANSITION", "0.15"))
GLOBAL_MIN_BRIGHT = int(os.getenv("ADDON_MIN_BRIGHT", "10"))
GLOBAL_MAX_BRIGHT = int(os.getenv("ADDON_MAX_BRIGHT", "255"))
COLOR_MODE = os.getenv("ADDON_COLOR_MODE", "spectrum")
GLOBAL_BASE_HUE = int(os.getenv("ADDON_BASE_HUE", "0"))
GLOBAL_BEAT_THRESH = float(os.getenv("ADDON_BEAT_THRESH", "0.55"))
SILENCE_S = int(os.getenv("ADDON_SILENCE_S", "8"))
RTMP_APP = os.getenv("ADDON_RTMP_APP", "live")
RTMP_STREAM = os.getenv("ADDON_RTMP_STREAM", "music")
LOG_LEVEL = os.getenv("ADDON_LOG_LEVEL", "info").upper()
WEBUI_PORT = int(os.getenv("ADDON_WEBUI_PORT", "8099"))

try:
    LEGACY_LIGHTS: List[str] = json.loads(os.getenv("ADDON_LIGHT_ENTITIES", "[]"))
except Exception:
    LEGACY_LIGHTS = []

try:
    LEGACY_AREAS: List[str] = json.loads(os.getenv("ADDON_AREA_IDS", "[]"))
except Exception:
    LEGACY_AREAS = []

try:
    PROFILES: List[Dict[str, Any]] = json.loads(os.getenv("ADDON_PROFILES", "[]"))
except Exception:
    PROFILES = []

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

# Populated in main() and shared with the Watchdog GUI thread.
state: Optional[WatchdogState] = None


def load_active_profile() -> Dict[str, Any]:
    if PROFILES:
        if ACTIVE_PROFILE:
            for p in PROFILES:
                if p.get("name") == ACTIVE_PROFILE:
                    log.info("Using profile: %s (room=%s)", p.get("name"), p.get("room"))
                    return p
            log.warning("active_profile '%s' not found - using first profile", ACTIVE_PROFILE)
        p = PROFILES[0]
        log.info("Using first profile: %s (room=%s)", p.get("name"), p.get("room"))
        return p

    log.info("No profiles defined - using legacy light_entities / area_ids")
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
            {
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            }
        )
        self._area_lights_cache: Dict[str, List[str]] = {}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{HA_URL}{path}"
        try:
            r = self.session.request(method, url, timeout=5, **kwargs)
            r.raise_for_status()
            if r.content:
                return r.json()
            return None
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
        ents = self._request("GET", "/config/entity_registry/list") or []
        for e in ents:
            eid = e.get("entity_id", "")
            if eid.startswith("light.") and e.get("area_id") in area_ids:
                entities.add(eid)

        if not entities:
            states = self._request("GET", "/states") or []
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
        payload: Dict[str, Any] = {
            "entity_id": entity_ids,
            "brightness": brightness,
            "transition": transition,
        }
        if hs_color is not None:
            payload["hs_color"] = list(hs_color)
        self._request("POST", "/services/light/turn_on", json=payload)

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
        if len(pcm) < BYTES_PER_CHUNK:
            return {"amplitude": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "beat": 0.0}

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        samples /= 32768.0

        window = np.hanning(len(samples))
        samples *= window

        fft = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)

        bass = self._band_energy(fft, freqs, 20, 150)
        mid = self._band_energy(fft, freqs, 150, 2000)
        high = self._band_energy(fft, freqs, 2000, 8000)
        amplitude = float(np.sqrt(np.mean(samples ** 2)))

        bass = min(1.0, bass * 8.0 * sensitivity)
        mid = min(1.0, mid * 6.0 * sensitivity)
        high = min(1.0, high * 5.0 * sensitivity)
        amplitude = min(1.0, amplitude * 4.0 * sensitivity)

        self.bass_hist.append(bass)
        self.energy_hist.append(amplitude)

        beat = 0.0
        now = time.time()
        if len(self.bass_hist) >= 5:
            avg = sum(self.bass_hist) / len(self.bass_hist)
            if bass > avg * (1.0 + self.beat_threshold) and (now - self.last_beat) > 0.25:
                beat = 1.0
                self.last_beat = now

        return {
            "amplitude": amplitude,
            "bass": bass,
            "mid": mid,
            "high": high,
            "beat": beat,
        }

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

    log.info(
        "Band lights - bass: %s | mid: %s | high: %s | full: %s",
        bass or "(none)",
        mid or "(none)",
        high or "(none)",
        full or "(none)",
    )
    return {"bass": bass, "mid": mid, "high": high, "full": full}


def map_to_lights(
    ha: HomeAssistant,
    analyzer: AudioAnalyzer,
    features: Dict[str, float],
    bands: Dict[str, List[str]],
    mode: str,
    min_b: int,
    max_b: int,
    transition: float,
):
    amp = features["amplitude"]
    bass = features["bass"]
    mid = features["mid"]
    high = features["high"]
    beat = features["beat"]

    if amp < 0.02:
        if analyzer.silence_start is None:
            analyzer.silence_start = time.time()
        elif time.time() - analyzer.silence_start > SILENCE_S:
            return
        return
    else:
        analyzer.silence_start = None

    def bright(val: float) -> int:
        return max(min_b, min(max_b, int(min_b + (max_b - min_b) * val)))

    if mode == "pulse":
        if beat > 0.5:
            targets = bands["bass"] or bands["full"]
            ha.set_lights(
                targets,
                max_b,
                hs_color=(analyzer.hue % 360, 90),
                transition=0.05,
            )
            analyzer.hue += 30
            if bands["mid"]:
                ha.set_lights(bands["mid"], bright(0.3), transition=transition)
            if bands["high"]:
                ha.set_lights(bands["high"], bright(0.2), transition=transition)
        else:
            targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
            ha.set_lights(targets, bright(amp * 0.5), transition=transition)

    elif mode == "spectrum":
        if bands["bass"]:
            ha.set_lights(
                bands["bass"],
                bright(bass),
                hs_color=(0, 80),
                transition=transition,
            )
        if bands["mid"]:
            ha.set_lights(
                bands["mid"],
                bright(mid),
                hs_color=(120, 70),
                transition=transition,
            )
        if bands["high"]:
            ha.set_lights(
                bands["high"],
                bright(high),
                hs_color=(240, 70),
                transition=transition,
            )
        if bands["full"]:
            hue = (bass * 0 + mid * 120 + high * 240) % 360
            ha.set_lights(
                bands["full"],
                bright(amp),
                hs_color=(hue, 60),
                transition=transition,
            )

    elif mode == "color_cycle":
        analyzer.hue = (analyzer.hue + 2 + bass * 8) % 360
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(
            targets,
            bright(amp),
            hs_color=(analyzer.hue, 80),
            transition=transition,
        )
        if bands["bass"] and bands["bass"] != targets:
            ha.set_lights(
                bands["bass"],
                bright(bass),
                hs_color=(analyzer.hue, 90),
                transition=transition,
            )

    elif mode == "brightness":
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(amp), transition=transition)
        if bands["bass"] and bands["bass"] != targets:
            ha.set_lights(bands["bass"], bright(bass), transition=transition)
        if bands["mid"] and bands["mid"] != targets:
            ha.set_lights(bands["mid"], bright(mid), transition=transition)
        if bands["high"] and bands["high"] != targets:
            ha.set_lights(bands["high"], bright(high), transition=transition)

    elif mode == "cinema":
        warm_hue = 30
        b = bright(0.3 + bass * 0.45)
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, b, hs_color=(warm_hue, 70), transition=0.3)

    else:
        targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
        ha.set_lights(targets, bright(amp), transition=transition)


def start_ffmpeg() -> subprocess.Popen:
    rtmp_url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-listen", "1",
        "-i", rtmp_url,
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "pipe:1",
    ]
    log.info("Starting FFmpeg RTMP listener: %s", rtmp_url)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=BYTES_PER_CHUNK * 4,
    )


def read_stderr(proc: subprocess.Popen):
    if proc.stderr is None:
        return
    for line in iter(proc.stderr.readline, b""):
        if line:
            log.debug("ffmpeg: %s", line.decode(errors="replace").rstrip())


def main():
    global state
    state = WatchdogState()
    state.set(enabled=ENABLED, rtmp_url=f"rtmp://<HA-IP>:1935/{RTMP_APP}/{RTMP_STREAM}")

    # Start the Watchdog GUI immediately so the ingress panel is reachable
    # even while the add-on is starting, disabled, or waiting for a stream.
    webui.start_server(state, port=WEBUI_PORT)

    if not TOKEN:
        msg = "SUPERVISOR_TOKEN missing - cannot talk to Home Assistant"
        log.error(msg)
        state.mark_error(msg)
        sys.exit(1)

    if not ENABLED:
        log.info("Add-on is disabled in configuration.")
        state.set(connection_state="disabled")
        state.log_event("Add-on ist in der Konfiguration deaktiviert.", "info")
        running = {"flag": True}

        def stop(signum, frame):
            running["flag"] = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while running["flag"]:
            time.sleep(1)
        state.mark_stopped()
        sys.exit(0)

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

    all_entities = list(
        set(bands["bass"] + bands["mid"] + bands["high"] + bands["full"])
    )

    state.set(
        profile_name=profile.get("name", ""),
        room=profile.get("room", ""),
        mode=mode,
        sensitivity=sensitivity,
        bands=bands,
        connection_state="waiting",
    )
    state.log_event(
        f"Add-on gestartet - Profil '{profile.get('name')}' / Modus '{mode}'", "success"
    )

    def shutdown(signum, frame):
        log.info("Shutting down...")
        analyzer.running = False
        ha.all_off(all_entities)
        state.mark_stopped()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info(
        "Active: profile=%s mode=%s sensitivity=%.2f",
        profile.get("name"),
        mode,
        sensitivity,
    )

    NO_SIGNAL_TIMEOUT_S = 4.0

    while analyzer.running:
        proc = start_ffmpeg()
        threading.Thread(target=read_stderr, args=(proc,), daemon=True).start()

        log.info("Waiting for Larix Broadcaster to connect...")
        state.mark_waiting()
        last_update = 0.0
        last_data_at = time.time()

        try:
            while analyzer.running and proc.poll() is None:
                chunk = proc.stdout.read(BYTES_PER_CHUNK)
                if not chunk:
                    if time.time() - last_data_at > NO_SIGNAL_TIMEOUT_S:
                        state.mark_no_signal()
                    time.sleep(0.05)
                    continue

                last_data_at = time.time()
                state.mark_chunk_received()

                features = analyzer.process(chunk, sensitivity)
                state.set_features(features)

                now = time.time()
                if (now - last_update) * 1000 >= UPDATE_MS:
                    map_to_lights(
                        ha, analyzer, features, bands, mode, min_b, max_b, transition
                    )
                    state.mark_light_update()
                    last_update = now
                    if LOG_LEVEL == "DEBUG":
                        log.debug(
                            "amp=%.2f bass=%.2f mid=%.2f high=%.2f beat=%.0f",
                            features["amplitude"],
                            features["bass"],
                            features["mid"],
                            features["high"],
                            features["beat"],
                        )
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
