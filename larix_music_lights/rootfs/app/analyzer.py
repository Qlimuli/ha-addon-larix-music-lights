#!/usr/bin/env python3
"""
Larix Music Reactive Lights – Audio analyzer & light controller
Receives RTMP stream (from Larix Broadcaster), performs real-time FFT,
and drives Home Assistant lights.
"""

import os
import sys
import json
import time
import math
import logging
import subprocess
import threading
import signal
from collections import deque
from typing import List, Optional, Dict, Any

import numpy as np
import requests

# ---------------------------------------------------------------------------
# Configuration from environment (set by run.sh / bashio)
# ---------------------------------------------------------------------------
ENABLED = os.getenv("ADDON_ENABLED", "true").lower() == "true"
MODE = os.getenv("ADDON_MODE", "pulse")
SENSITIVITY = float(os.getenv("ADDON_SENSITIVITY", "0.7"))
UPDATE_MS = int(os.getenv("ADDON_UPDATE_MS", "80"))
TRANSITION = float(os.getenv("ADDON_TRANSITION", "0.15"))
MIN_BRIGHT = int(os.getenv("ADDON_MIN_BRIGHT", "10"))
MAX_BRIGHT = int(os.getenv("ADDON_MAX_BRIGHT", "255"))
COLOR_MODE = os.getenv("ADDON_COLOR_MODE", "spectrum")
BASE_HUE = int(os.getenv("ADDON_BASE_HUE", "0"))
BEAT_THRESH = float(os.getenv("ADDON_BEAT_THRESH", "0.55"))
SILENCE_S = int(os.getenv("ADDON_SILENCE_S", "8"))
RTMP_APP = os.getenv("ADDON_RTMP_APP", "live")
RTMP_STREAM = os.getenv("ADDON_RTMP_STREAM", "music")
LOG_LEVEL = os.getenv("ADDON_LOG_LEVEL", "info").upper()

try:
    LIGHT_ENTITIES: List[str] = json.loads(os.getenv("ADDON_LIGHT_ENTITIES", "[]"))
except Exception:
    LIGHT_ENTITIES = []

try:
    AREA_IDS: List[str] = json.loads(os.getenv("ADDON_AREA_IDS", "[]"))
except Exception:
    AREA_IDS = []

# Home Assistant API via Supervisor
HA_URL = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")

# Audio parameters
SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
CHUNK_SAMPLES = 2048  # ~46 ms at 44.1 kHz
BYTES_PER_CHUNK = CHUNK_SAMPLES * SAMPLE_WIDTH * CHANNELS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("larix-music")

# ---------------------------------------------------------------------------
# Home Assistant helper
# ---------------------------------------------------------------------------
class HomeAssistant:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            }
        )
        self._resolved_entities: Optional[List[str]] = None

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

    def resolve_entities(self) -> List[str]:
        """Combine explicit light entities + lights belonging to selected areas."""
        if self._resolved_entities is not None:
            return self._resolved_entities

        entities = set(LIGHT_ENTITIES)

        if AREA_IDS:
            states = self._request("GET", "/states") or []
            for s in states:
                eid = s.get("entity_id", "")
                if not eid.startswith("light."):
                    continue
                attrs = s.get("attributes", {})
                area = attrs.get("area_id") or attrs.get("area")
                if area in AREA_IDS:
                    entities.add(eid)

            # Fallback: try area registry
            areas = self._request("GET", "/config/area_registry/list") or []
            area_map = {a["area_id"]: a.get("name") for a in areas}
            # entity registry
            ents = self._request("GET", "/config/entity_registry/list") or []
            for e in ents:
                if e.get("entity_id", "").startswith("light.") and e.get("area_id") in AREA_IDS:
                    entities.add(e["entity_id"])

        self._resolved_entities = sorted(entities)
        log.info("Controlling lights: %s", self._resolved_entities or "(none configured)")
        return self._resolved_entities

    def turn_on(self, entity_id: str, **kwargs):
        data = {"entity_id": entity_id, **kwargs}
        self._request("POST", "/services/light/turn_on", json=data)

    def turn_off(self, entity_id: str, transition: float = 1.0):
        self._request(
            "POST",
            "/services/light/turn_off",
            json={"entity_id": entity_id, "transition": transition},
        )

    def set_lights(self, brightness: int, hs_color: Optional[tuple] = None, transition: float = TRANSITION):
        entities = self.resolve_entities()
        if not entities:
            return
        payload: Dict[str, Any] = {
            "entity_id": entities,
            "brightness": max(MIN_BRIGHT, min(MAX_BRIGHT, brightness)),
            "transition": transition,
        }
        if hs_color is not None:
            payload["hs_color"] = list(hs_color)
        self._request("POST", "/services/light/turn_on", json=payload)

    def all_off(self):
        entities = self.resolve_entities()
        if entities:
            self._request(
                "POST",
                "/services/light/turn_off",
                json={"entity_id": entities, "transition": 1.5},
            )


# ---------------------------------------------------------------------------
# Audio analysis
# ---------------------------------------------------------------------------
class AudioAnalyzer:
    def __init__(self):
        self.bass_hist = deque(maxlen=30)
        self.energy_hist = deque(maxlen=20)
        self.last_beat = 0.0
        self.hue = float(BASE_HUE)
        self.silence_start: Optional[float] = None
        self.running = True

    def process(self, pcm: bytes) -> Dict[str, float]:
        """Return dict with keys: amplitude, bass, mid, high, beat (0/1)."""
        if len(pcm) < BYTES_PER_CHUNK:
            return {"amplitude": 0.0, "bass": 0.0, "mid": 0.0, "high": 0.0, "beat": 0.0}

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        samples /= 32768.0

        # Window
        window = np.hanning(len(samples))
        samples *= window

        # FFT
        fft = np.abs(np.fft.rfft(samples))
        freqs = np.fft.rfftfreq(len(samples), 1.0 / SAMPLE_RATE)

        # Bands (approximate)
        bass = self._band_energy(fft, freqs, 20, 150)
        mid = self._band_energy(fft, freqs, 150, 2000)
        high = self._band_energy(fft, freqs, 2000, 8000)
        amplitude = float(np.sqrt(np.mean(samples ** 2)))  # RMS

        # Normalize roughly
        bass = min(1.0, bass * 8.0 * SENSITIVITY)
        mid = min(1.0, mid * 6.0 * SENSITIVITY)
        high = min(1.0, high * 5.0 * SENSITIVITY)
        amplitude = min(1.0, amplitude * 4.0 * SENSITIVITY)

        self.bass_hist.append(bass)
        self.energy_hist.append(amplitude)

        # Simple beat detection on bass
        beat = 0.0
        now = time.time()
        if len(self.bass_hist) >= 5:
            avg = sum(self.bass_hist) / len(self.bass_hist)
            if bass > avg * (1.0 + BEAT_THRESH) and (now - self.last_beat) > 0.25:
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


# ---------------------------------------------------------------------------
# Light control logic per mode
# ---------------------------------------------------------------------------
def map_to_lights(ha: HomeAssistant, analyzer: AudioAnalyzer, features: Dict[str, float]):
    amp = features["amplitude"]
    bass = features["bass"]
    mid = features["mid"]
    high = features["high"]
    beat = features["beat"]

    if amp < 0.02:
        if analyzer.silence_start is None:
            analyzer.silence_start = time.time()
        elif time.time() - analyzer.silence_start > SILENCE_S:
            # stay off / dim
            return
        return
    else:
        analyzer.silence_start = None

    brightness = int(MIN_BRIGHT + (MAX_BRIGHT - MIN_BRIGHT) * amp)

    if MODE == "pulse":
        if beat > 0.5:
            brightness = MAX_BRIGHT
            ha.set_lights(brightness, hs_color=(analyzer.hue % 360, 90), transition=0.05)
            analyzer.hue += 30
        else:
            # decay
            brightness = max(MIN_BRIGHT, int(brightness * 0.6))
            ha.set_lights(brightness, transition=TRANSITION)

    elif MODE == "spectrum":
        # Map bass→red, mid→green, high→blue-ish via hue
        hue = (bass * 0 + mid * 120 + high * 240) % 360
        sat = min(100, 40 + high * 60)
        ha.set_lights(brightness, hs_color=(hue, sat), transition=TRANSITION)

    elif MODE == "color_cycle":
        analyzer.hue = (analyzer.hue + 2 + bass * 8) % 360
        ha.set_lights(brightness, hs_color=(analyzer.hue, 80), transition=TRANSITION)

    elif MODE == "brightness":
        ha.set_lights(brightness, transition=TRANSITION)

    elif MODE == "cinema":
        # Warm dim + slight pulse on bass
        warm_hue = 30
        b = int(MIN_BRIGHT + (MAX_BRIGHT * 0.45 - MIN_BRIGHT) * (0.3 + bass * 0.7))
        ha.set_lights(b, hs_color=(warm_hue, 70), transition=0.3)

    else:
        ha.set_lights(brightness, transition=TRANSITION)


# ---------------------------------------------------------------------------
# FFmpeg RTMP listener → raw PCM pipe
# ---------------------------------------------------------------------------
def start_ffmpeg() -> subprocess.Popen:
    """
    Listen for an incoming RTMP stream and output raw 16-bit mono PCM.
    Larix connects to: rtmp://<ha-ip>:1935/<app>/<stream>
    """
    rtmp_url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-listen", "1",
        "-i", rtmp_url,
        "-vn",                      # no video
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
    """Background thread to log ffmpeg stderr."""
    if proc.stderr is None:
        return
    for line in iter(proc.stderr.readline, b""):
        if line:
            log.debug("ffmpeg: %s", line.decode(errors="replace").rstrip())


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    if not TOKEN:
        log.error("SUPERVISOR_TOKEN missing – cannot talk to Home Assistant")
        sys.exit(1)

    if not ENABLED:
        log.info("Add-on is disabled in configuration. Exiting.")
        sys.exit(0)

    ha = HomeAssistant()
    analyzer = AudioAnalyzer()

    # Resolve entities once at start
    ha.resolve_entities()

    def shutdown(signum, frame):
        log.info("Shutting down…")
        analyzer.running = False
        ha.all_off()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while analyzer.running:
        proc = start_ffmpeg()
        threading.Thread(target=read_stderr, args=(proc,), daemon=True).start()

        log.info("Waiting for Larix Broadcaster to connect…")
        last_update = 0.0

        try:
            while analyzer.running and proc.poll() is None:
                chunk = proc.stdout.read(BYTES_PER_CHUNK)
                if not chunk:
                    time.sleep(0.05)
                    continue

                features = analyzer.process(chunk)
                now = time.time()
                if (now - last_update) * 1000 >= UPDATE_MS:
                    map_to_lights(ha, analyzer, features)
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
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()

        log.warning("FFmpeg exited – restarting in 3 s…")
        time.sleep(3)


if __name__ == "__main__":
    main()
