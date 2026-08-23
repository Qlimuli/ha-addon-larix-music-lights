#!/usr/bin/env python3
"""Larix Music Reactive Lights analyzer v1.4.2 – Zigbee-friendly"""
import os, sys, json, time, logging, subprocess, threading, signal, colorsys
from collections import deque
from typing import List, Optional, Dict, Any
import numpy as np
import requests

try:
    import webui
    from watchdog_state import WatchdogState
    _HAS_WEBUI = True
except Exception as e:
    webui = WatchdogState = None
    _HAS_WEBUI = False
    print(f"WARNING: webui not available: {e}", flush=True)

def _opt():
    for p in (os.getenv("ADDON_OPTIONS_FILE",""), "/data/options.json", "/config/options.json"):
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    return d
            except Exception as e:
                print("WARNING options", e, flush=True)
    return {}

O = _opt()
ENABLED = bool(O.get("enabled", True))
ACTIVE_PROFILE = str(O.get("active_profile") or "").strip()
GLOBAL_MODE = str(O.get("mode") or "auto")
GLOBAL_SENSITIVITY = float(O.get("sensitivity", 1.2))
UPDATE_MS = max(int(O.get("update_interval_ms", 220)), 180)
GLOBAL_TRANSITION = float(O.get("transition", 0.15))
GLOBAL_MIN_BRIGHT = int(O.get("min_brightness", 30))
GLOBAL_MAX_BRIGHT = int(O.get("max_brightness", 255))
GLOBAL_BASE_HUE = int(O.get("base_hue", 0))
GLOBAL_BEAT_THRESH = float(O.get("beat_threshold", 0.55))
SILENCE_S = int(O.get("silence_timeout_s", 8))
RTMP_APP = str(O.get("rtmp_app") or "live")
RTMP_STREAM = str(O.get("rtmp_stream") or "music")
LOG_LEVEL = str(O.get("log_level") or "info").upper()
WEBUI_PORT = int(os.getenv("ADDON_WEBUI_PORT", "8099"))
LEGACY_LIGHTS = [str(x) for x in (O.get("light_entities") or [])]
LEGACY_AREAS = [str(x) for x in (O.get("area_ids") or [])]
PROFILES = [p for p in (O.get("profiles") or []) if isinstance(p, dict)]
HA_URL = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH, CHUNK_SAMPLES = 44100, 1, 2, 2048
BYTES_PER_CHUNK = CHUNK_SAMPLES * SAMPLE_WIDTH * CHANNELS

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("larix-music")
log.info("Config loaded: lights=%s profiles=%d interval=%dms", LEGACY_LIGHTS, len(PROFILES), UPDATE_MS)
state = None

def load_active_profile():
    if PROFILES:
        if ACTIVE_PROFILE:
            for p in PROFILES:
                if p.get("name") == ACTIVE_PROFILE:
                    return p
        return PROFILES[0] if isinstance(PROFILES[0], dict) else {}
    return {
        "name": "legacy", "room": "", "area_ids": LEGACY_AREAS, "mode": GLOBAL_MODE,
        "sensitivity": GLOBAL_SENSITIVITY, "min_brightness": GLOBAL_MIN_BRIGHT,
        "max_brightness": GLOBAL_MAX_BRIGHT, "transition": GLOBAL_TRANSITION,
        "beat_threshold": GLOBAL_BEAT_THRESH, "base_hue": GLOBAL_BASE_HUE,
        "bass_lights": [], "mid_lights": [], "high_lights": [], "full_lights": LEGACY_LIGHTS,
    }

def pv(profile, key, default):
    v = profile.get(key)
    return default if v is None else v

class HomeAssistant:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        self._area_cache = {}

    def _req(self, method, path, **kw):
        try:
            r = self.session.request(method, f"{HA_URL}{path}", timeout=5, **kw)
            r.raise_for_status()
            return r.json() if r.content else True
        except Exception as e:
            log.warning("HA API %s %s: %s", method, path, e)
            return None

    def lights_for_areas(self, area_ids):
        if not area_ids:
            return []
        key = ",".join(sorted(area_ids))
        if key in self._area_cache:
            return self._area_cache[key]
        entities = set()
        for s in (self._req("GET", "/states") or []):
            eid = s.get("entity_id", "")
            if not eid.startswith("light."):
                continue
            a = s.get("attributes", {})
            if (a.get("area_id") or a.get("area")) in area_ids:
                entities.add(eid)
        self._area_cache[key] = sorted(entities)
        return self._area_cache[key]

    def set_lights(self, entity_ids, brightness, hs_color=None, transition=0.15):
        if not entity_ids:
            return
        brightness = max(1, min(255, int(brightness)))
        payload = {"entity_id": entity_ids, "brightness": brightness, "transition": max(0.0, float(transition))}
        if hs_color is not None:
            try:
                h = (float(hs_color[0]) % 360) / 360.0
                s = max(0.0, min(1.0, float(hs_color[1]) / 100.0))
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                def lin(c):
                    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
                r2, g2, b2 = lin(r), lin(g), lin(b)
                X = r2 * 0.4124 + g2 * 0.3576 + b2 * 0.1805
                Y = r2 * 0.2126 + g2 * 0.7152 + b2 * 0.0722
                Z = r2 * 0.0193 + g2 * 0.1192 + b2 * 0.9505
                tot = X + Y + Z
                if tot > 1e-6:
                    payload["xy_color"] = [round(X / tot, 4), round(Y / tot, 4)]
            except Exception:
                payload["hs_color"] = [float(hs_color[0]) % 360, float(hs_color[1])]
        if self._req("POST", "/services/light/turn_on", json=payload) is None:
            self._req("POST", "/services/light/turn_on",
                      json={"entity_id": entity_ids, "brightness": brightness, "transition": max(0.0, float(transition))})

    def all_off(self, entity_ids, transition=1.5):
        if entity_ids:
            self._req("POST", "/services/light/turn_off", json={"entity_id": entity_ids, "transition": transition})

class AudioAnalyzer:
    def __init__(self, beat_threshold, base_hue):
        self.bass_hist = deque(maxlen=30)
        self.peak_hist = deque(maxlen=80)
        self.last_beat = 0.0
        self.hue = float(base_hue)
        self.beat_threshold = beat_threshold
        self.silence_start = None
        self.running = True
        self._last_send = 0.0
        self._last_bri = -1
        self._last_hue = -1.0
        self._last_sat = -1.0

    def process(self, pcm, sensitivity):
        if len(pcm) < 1024:
            return dict(amplitude=0, bass=0, mid=0, high=0, beat=0)
        if len(pcm) % 2:
            pcm = pcm[:-1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if not samples.size:
            return dict(amplitude=0, bass=0, mid=0, high=0, beat=0)
        peak = float(np.max(np.abs(samples)))
        samples /= 32768.0
        w = samples * np.hanning(len(samples))
        fft = np.abs(np.fft.rfft(w))
        freqs = np.fft.rfftfreq(len(w), 1.0 / SAMPLE_RATE)
        bass_raw = self._band(fft, freqs, 20, 150)
        mid_raw = self._band(fft, freqs, 150, 2000)
        high_raw = self._band(fft, freqs, 2000, 8000)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        self.peak_hist.append(peak)
        ref = max(max(self.peak_hist), 400.0)
        amp = min(1.0, (peak / ref) * 0.95 * sensitivity)
        amp = max(amp, min(1.0, rms * 25.0 * sensitivity))
        scale = (sensitivity * 8.0) / max(ref / 3000.0, 0.35)
        bass = min(1.0, bass_raw * scale)
        mid = min(1.0, mid_raw * scale * 0.9)
        high = min(1.0, high_raw * scale * 0.8)
        self.bass_hist.append(bass)
        beat = 0.0
        now = time.time()
        if len(self.bass_hist) >= 5:
            avg = sum(self.bass_hist) / len(self.bass_hist)
            if bass > avg * (1.0 + self.beat_threshold) and (now - self.last_beat) > 0.28:
                beat = 1.0
                self.last_beat = now
        return dict(amplitude=amp, bass=bass, mid=mid, high=high, beat=beat)

    @staticmethod
    def _band(fft, freqs, lo, hi):
        m = (freqs >= lo) & (freqs < hi)
        return float(np.mean(fft[m])) if np.any(m) else 0.0

def resolve_bands(ha, profile):
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
    return dict(bass=bass, mid=mid, high=high, full=full)

def map_to_lights(ha, analyzer, features, bands, mode, min_b, max_b, transition, min_interval_s=0.22):
    amp, bass, mid, high, beat = features["amplitude"], features["bass"], features["mid"], features["high"], features["beat"]
    if amp < 0.03 and bass < 0.05:
        if analyzer.silence_start is None:
            analyzer.silence_start = time.time()
        return
    analyzer.silence_start = None

    def bright(val):
        return max(min_b, min(max_b, int(min_b + (max_b - min_b) * val)))

    def should_send(bri, hue, sat, force=False):
        now = time.time()
        if force:
            analyzer._last_send, analyzer._last_bri, analyzer._last_hue, analyzer._last_sat = now, bri, hue, sat
            return True
        if (now - analyzer._last_send) < min_interval_s:
            return False
        if abs(bri - analyzer._last_bri) < 18 and abs(hue - analyzer._last_hue) < 18 and abs(sat - analyzer._last_sat) < 12:
            return False
        analyzer._last_send, analyzer._last_bri, analyzer._last_hue, analyzer._last_sat = now, bri, hue, sat
        return True

    targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]

    if mode in ("auto", "automatic", "pulse"):
        energy = min(1.0, amp * 0.70 + bass * 0.45 + mid * 0.25 + high * 0.10)
        level = max(0.35, min(1.0, (energy ** 0.55) * 1.15))
        total = bass + mid + high + 1e-6
        hue = (bass / total * 5 + mid / total * 140 + high / total * 260) % 360
        sat = 40 + min(55, bass * 35 + high * 25)
        extreme = (beat > 0.5) or (bass >= 0.75 and amp >= 0.25)
        if extreme:
            if should_send(max_b, 0.0, 0.0, force=True):
                ha.set_lights(targets, max_b, hs_color=(0, 0), transition=0.0)
            analyzer.hue = hue
        else:
            bri = bright(level)
            if should_send(bri, hue, sat):
                ha.set_lights(targets, bri, hs_color=(hue, sat), transition=min(transition, 0.15))
            analyzer.hue = hue
        return

    if mode == "spectrum":
        if bands["bass"]:
            ha.set_lights(bands["bass"], bright(bass), hs_color=(0, 80), transition=transition)
        if bands["mid"]:
            ha.set_lights(bands["mid"], bright(mid), hs_color=(120, 70), transition=transition)
        if bands["high"]:
            ha.set_lights(bands["high"], bright(high), hs_color=(240, 70), transition=transition)
        if bands["full"]:
            hue = (mid * 120 + high * 240) % 360
            ha.set_lights(bands["full"], bright(amp), hs_color=(hue, 60), transition=transition)
        return

    if mode == "color_cycle":
        analyzer.hue = (analyzer.hue + 2 + bass * 8) % 360
        bri = bright(max(0.35, amp))
        if should_send(bri, analyzer.hue, 80):
            ha.set_lights(targets, bri, hs_color=(analyzer.hue, 80), transition=min(transition, 0.15))
        return

    if mode == "cinema":
        bri = bright(0.35 + bass * 0.5)
        if should_send(bri, 30, 70):
            ha.set_lights(targets, bri, hs_color=(30, 70), transition=0.25)
        return

    bri = bright(max(0.35, amp))
    if should_send(bri, 0, 0):
        ha.set_lights(targets, bri, transition=min(transition, 0.15))

def start_ffmpeg():
    url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = ["ffmpeg","-hide_banner","-loglevel","info","-listen","1","-timeout","0","-i",url,
           "-vn","-ac",str(CHANNELS),"-ar",str(SAMPLE_RATE),"-f","s16le","-acodec","pcm_s16le","pipe:1"]
    log.info("Starting FFmpeg RTMP listener: %s", url)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

def read_stderr(proc):
    if not proc.stderr:
        return
    for line in iter(proc.stderr.readline, b""):
        if not line:
            continue
        text = line.decode(errors="replace").rstrip()
        if not text or text.startswith("size="):
            continue
        low = text.lower()
        (log.warning if any(k in low for k in ("error","fail","invalid","unable")) else log.info)("ffmpeg: %s", text)

class _Null:
    def set(self, **k): pass
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
    state = WatchdogState() if _HAS_WEBUI else _Null()
    if _HAS_WEBUI:
        state.set(enabled=ENABLED, rtmp_url=f"rtmp://<HA-IP>:1935/{RTMP_APP}/{RTMP_STREAM}")
        webui.start_server(state, port=WEBUI_PORT)
    if not TOKEN:
        log.error("SUPERVISOR_TOKEN missing"); sys.exit(1)
    if not ENABLED:
        log.info("Add-on disabled")
        while True:
            time.sleep(1)
    profile = load_active_profile()
    mode = str(pv(profile, "mode", GLOBAL_MODE))
    sensitivity = float(pv(profile, "sensitivity", GLOBAL_SENSITIVITY))
    min_b = int(pv(profile, "min_brightness", GLOBAL_MIN_BRIGHT))
    max_b = int(pv(profile, "max_brightness", GLOBAL_MAX_BRIGHT))
    transition = float(pv(profile, "transition", GLOBAL_TRANSITION))
    beat_thresh = float(pv(profile, "beat_threshold", GLOBAL_BEAT_THRESH))
    base_hue = float(pv(profile, "base_hue", GLOBAL_BASE_HUE))
    min_interval_s = UPDATE_MS / 1000.0
    ha = HomeAssistant()
    bands = resolve_bands(ha, profile)
    analyzer = AudioAnalyzer(beat_thresh, base_hue)
    all_entities = list(set(bands["bass"] + bands["mid"] + bands["high"] + bands["full"]))
    state.set(profile_name=profile.get("name",""), room=profile.get("room",""),
              mode=mode, sensitivity=sensitivity, bands=bands, connection_state="waiting")
    def shutdown(s, f):
        analyzer.running = False
        ha.all_off(all_entities)
        state.mark_stopped(); sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    log.info("Active: mode=%s sensitivity=%.2f interval=%dms lights=%s", mode, sensitivity, UPDATE_MS, all_entities)
    while analyzer.running:
        proc = start_ffmpeg()
        threading.Thread(target=read_stderr, args=(proc,), daemon=True).start()
        log.info("Waiting for Larix Broadcaster to connect...")
        state.mark_waiting()
        last_update = last_log_sec = -1
        last_data_at = time.time()
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
                    frame = bytes(pcm_buf[:BYTES_PER_CHUNK]); del pcm_buf[:BYTES_PER_CHUNK]
                    features = analyzer.process(frame, sensitivity)
                    state.set_features(features)
                    now = time.time()
                    if (now - last_update) * 1000 >= UPDATE_MS:
                        map_to_lights(ha, analyzer, features, bands, mode, min_b, max_b, transition, min_interval_s)
                        state.mark_light_update()
                        sec = int(now)
                        if sec != last_log_sec and sec % 2 == 0:
                            try:
                                peak = int(np.max(np.abs(np.frombuffer(frame, dtype=np.int16))))
                            except Exception:
                                peak = 0
                            log.info("audio amp=%.3f bass=%.3f mid=%.3f high=%.3f beat=%.0f peak=%d mode=%s",
                                     features["amplitude"], features["bass"], features["mid"],
                                     features["high"], features["beat"], peak, mode)
                            last_log_sec = sec
                        last_update = now
        except Exception as e:
            log.error("Main loop error: %s", e); state.mark_error(str(e))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill()
        if analyzer.running:
            log.warning("FFmpeg exited - restarting in 3 s...")
            state.mark_ffmpeg_restart(); time.sleep(3)

if __name__ == "__main__":
    main()
