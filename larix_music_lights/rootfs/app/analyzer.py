#!/usr/bin/env python3
"""Larix Music Reactive Lights v1.6.2 – colorful auto patterns"""
import os, sys, json, time, logging, subprocess, threading, signal, colorsys, hashlib
from collections import deque
from typing import Dict, List
import numpy as np
import requests

try:
    import webui
    from watchdog_state import WatchdogState
    _HAS_WEBUI = True
except Exception as e:
    webui = WatchdogState = None
    _HAS_WEBUI = False
    print("WARNING webui:", e, flush=True)

def _opt():
    for p in (os.getenv("ADDON_OPTIONS_FILE",""), "/data/options.json", "/config/options.json"):
        if p and os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict): return d
            except Exception as e:
                print("WARNING options", e, flush=True)
    return {}

O = _opt()
ENABLED = bool(O.get("enabled", True))
MODE = str(O.get("mode") or "auto")
SENS = float(O.get("sensitivity", 1.2))
UPDATE_MS = max(int(O.get("update_interval_ms", 180)), 120)
TRANS = float(O.get("transition", 0.05))
MIN_B = int(O.get("min_brightness", 25))
MAX_B = int(O.get("max_brightness", 255))
BEAT_T = float(O.get("beat_threshold", 0.4))
RTMP_APP = str(O.get("rtmp_app") or "live")
RTMP_STREAM = str(O.get("rtmp_stream") or "music")
LOG_LEVEL = str(O.get("log_level") or "info").upper()
LIGHTS = [str(x) for x in (O.get("full_lights") or O.get("light_entities") or [])]
BASS_L = [str(x) for x in (O.get("bass_lights") or [])]
MID_L = [str(x) for x in (O.get("mid_lights") or [])]
HIGH_L = [str(x) for x in (O.get("high_lights") or [])]
HA_URL = "http://supervisor/core/api"
TOKEN = os.getenv("SUPERVISOR_TOKEN", "")
SR, CH, SW, CS = 44100, 1, 2, 2048
BPC = CS * SW * CH
WEBUI_PORT = int(os.getenv("ADDON_WEBUI_PORT", "8099"))

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("larix-music")
log.info("Config: lights=%s bass=%s mid=%s high=%s interval=%dms mode=%s",
         LIGHTS, BASS_L, MID_L, HIGH_L, UPDATE_MS, MODE)
state = None

class HA:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        self._lock = threading.Lock()
        self._n = 0
        self._max = 4

    def _req(self, method, path, **kw):
        try:
            r = self.s.request(method, f"{HA_URL}{path}", timeout=1.2, **kw)
            r.raise_for_status()
            return r.json() if r.content else True
        except Exception as e:
            log.debug("HA %s %s: %s", method, path, e)
            return None

    def fire(self, method, path, **kw):
        with self._lock:
            if self._n >= self._max:
                return
            self._n += 1
        def w():
            try:
                self._req(method, path, **kw)
            finally:
                with self._lock:
                    self._n = max(0, self._n - 1)
        threading.Thread(target=w, daemon=True).start()

    def set_lights(self, eids, bri, hs=None, trans=0.05):
        if not eids:
            return
        bri = max(1, min(255, int(bri)))
        p = {"entity_id": list(eids), "brightness": bri, "transition": max(0.0, float(trans))}
        if hs is not None:
            try:
                h = (float(hs[0]) % 360) / 360.0
                s = max(0.0, min(1.0, float(hs[1]) / 100.0))
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                def lin(c):
                    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
                r2, g2, b2 = lin(r), lin(g), lin(b)
                X = r2 * 0.4124 + g2 * 0.3576 + b2 * 0.1805
                Y = r2 * 0.2126 + g2 * 0.7152 + b2 * 0.0722
                Z = r2 * 0.0193 + g2 * 0.1192 + b2 * 0.9505
                t = X + Y + Z
                if t > 1e-6:
                    p["xy_color"] = [round(X / t, 4), round(Y / t, 4)]
            except Exception:
                p["hs_color"] = [float(hs[0]) % 360, float(hs[1])]
        self.fire("POST", "/services/light/turn_on", json=p)

    def off(self, eids, trans=1.5):
        if eids:
            self.fire("POST", "/services/light/turn_off",
                      json={"entity_id": list(eids), "transition": trans})

class Analyzer:
    def __init__(self, bt):
        self.bh = deque(maxlen=48)
        self.eh = deque(maxlen=48)
        self.ph = deque(maxlen=120)
        self.last_beat = 0.0
        self.hue = 0.0
        self.bt = bt
        self.running = True
        self._last = {}
        self._bm = self._mm = self._hm = 1e-6
        self._hold_bri = None
        self._hold_hue = 200.0
        self._hold_sat = 40.0
        self._sustain_t0 = 0.0
        self._post_beat_until = 0.0
        self._pattern_phase = 0.0
        self._last_palette_hue = 200.0

    def process(self, pcm, sens):
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
        freqs = np.fft.rfftfreq(len(w), 1.0 / SR)
        def band(lo, hi):
            m = (freqs >= lo) & (freqs < hi)
            return float(np.mean(fft[m])) if np.any(m) else 0.0
        br, mr, hr = band(20, 180), band(180, 2000), band(2000, 9000)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        self.ph.append(peak)
        pref = max(max(self.ph), 500.0)
        amp = min(1.0, (peak / pref) * sens)
        amp = max(amp, min(1.0, rms * 22 * sens))
        d = 0.988
        self._bm = max(br, self._bm * d)
        self._mm = max(mr, self._mm * d)
        self._hm = max(hr, self._hm * d)
        bass = min(1.0, (br / (self._bm + 1e-12)) ** 0.75 * (0.85 + 0.25 * sens))
        mid = min(1.0, (mr / (self._mm + 1e-12)) ** 0.75 * (0.85 + 0.25 * sens))
        high = min(1.0, (hr / (self._hm + 1e-12)) ** 0.75 * (0.85 + 0.25 * sens))
        self.bh.append(bass)
        self.eh.append(amp)
        beat = 0.0
        now = time.time()
        if len(self.eh) >= 8 and (now - self.last_beat) > 0.22:
            ea = sum(self.eh) / len(self.eh)
            ba = sum(self.bh) / len(self.bh)
            onset = (amp > ea * (1.25 + self.bt * 0.4) and amp > 0.22) or \
                    (bass > ba * 1.45 and bass > 0.60 and amp > 0.15) or \
                    (peak > pref * 0.60 and amp > 0.35)
            if onset:
                beat = 1.0
                self.last_beat = now
        return dict(amplitude=amp, bass=bass, mid=mid, high=high, beat=beat)

    def ok(self, eid, bri, hue, sat, min_iv, force=False):
        now = time.time()
        prev = self._last.get(eid)
        if force:
            self._last[eid] = (bri, hue, sat, now)
            return True
        if prev is None:
            self._last[eid] = (bri, hue, sat, now)
            return True
        pb, ph, ps, pt = prev
        if (now - pt) < min_iv:
            return False
        if abs(bri - pb) < 18 and abs(hue - ph) < 18 and abs(sat - ps) < 12:
            return False
        self._last[eid] = (bri, hue, sat, now)
        return True

def map_lights(ha, az, f, bands, mode, min_b, max_b, trans, iv):
    amp, bass, mid, high, beat = f["amplitude"], f["bass"], f["mid"], f["high"], f["beat"]
    if amp < 0.04 and bass < 0.06 and beat < 0.5:
        return

    def bright(v):
        return max(min_b, min(max_b, int(min_b + (max_b - min_b) * v)))

    targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
    if not targets and not (bands["bass"] or bands["mid"] or bands["high"]):
        return

    if mode == "auto":
        now = time.time()
        tot = bass + mid + high + 1e-6
        az._pattern_phase = (az._pattern_phase + 0.8 + amp * 2.5 + beat * 12) % 360
        spectrum_hue = (bass / tot * 15 + mid / tot * 150 + high / tot * 265) % 360
        base_hue = (0.65 * spectrum_hue + 0.35 * az._pattern_phase) % 360
        base_sat = 40 + min(50, bass * 20 + mid * 25 + high * 30)

        energy = min(1.0, amp * 0.55 + bass * 0.35 + mid * 0.2)
        ambient = bright(0.20 + 0.35 * energy)

        loud = energy >= 0.72 or amp >= 0.85
        if loud:
            if az._sustain_t0 <= 0:
                az._sustain_t0 = now
        else:
            az._sustain_t0 = 0.0
        sustain_s = (now - az._sustain_t0) if az._sustain_t0 > 0 else 0.0

        if beat > 0.5:
            for e in targets:
                if az.ok(e, max_b, 0, 0, 0.0, force=True):
                    ha.set_lights([e], max_b, hs=(0, 0), trans=0.0)
            az._post_beat_until = now + 0.28
            az._hold_bri = max_b
            az._hold_hue = base_hue
            az._hold_sat = min(90, base_sat + 15)
            az._last_palette_hue = base_hue
            return

        if now < az._post_beat_until:
            bloom_bri = bright(0.75 + 0.25 * energy)
            for e in targets:
                if az.ok(e, bloom_bri, base_hue, min(95, base_sat + 20), 0.12, force=True):
                    ha.set_lights([e], bloom_bri, hs=(base_hue, min(95, base_sat + 20)), trans=0.06)
            az._hold_bri = bloom_bri
            az._hold_hue = base_hue
            az._hold_sat = min(95, base_sat + 20)
            return

        if sustain_s >= 0.45:
            if sustain_s < 0.9:
                t = min(1.0, (sustain_s - 0.45) / 0.45)
                sat = int(8 + t * base_sat)
                hue = base_hue
            else:
                hue = (base_hue + (sustain_s - 0.9) * 80) % 360
                sat = min(95, int(base_sat + 15))
            bri = max_b if energy > 0.8 else bright(0.9)
            min_iv = max(0.18, iv * 0.9)
            for e in targets:
                if az.ok(e, bri, hue, sat, min_iv, force=False):
                    ha.set_lights([e], bri, hs=(hue, sat), trans=0.08)
            az._hold_bri = bri
            az._hold_hue = hue
            az._hold_sat = sat
            return

        if az._hold_bri is None:
            az._hold_bri = ambient
            az._hold_hue = base_hue
            az._hold_sat = base_sat
        target_bri = bright(0.25 + 0.55 * energy)
        az._hold_bri = int(0.55 * az._hold_bri + 0.45 * target_bri)
        dh = ((base_hue - az._hold_hue + 540) % 360) - 180
        az._hold_hue = (az._hold_hue + dh * 0.35) % 360
        az._hold_sat = 0.65 * az._hold_sat + 0.35 * base_sat
        min_iv = max(0.28, iv * 1.2)
        for e in targets:
            if az.ok(e, az._hold_bri, az._hold_hue, az._hold_sat, min_iv, force=False):
                ha.set_lights([e], az._hold_bri, hs=(az._hold_hue, az._hold_sat), trans=0.1)
        return

    if mode == "pulse":
        if beat > 0.5:
            for e in targets:
                if az.ok(e, max_b, 0, 0, 0.0, force=True):
                    ha.set_lights([e], max_b, hs=(0, 0), trans=0.0)
        else:
            b = bright(0.25 + 0.2 * amp)
            for e in targets:
                if az.ok(e, b, 0, 0, max(0.5, iv * 1.5)):
                    ha.set_lights([e], b, hs=(0, 0), trans=0.1)
        return

    if mode == "spectrum" or (bands["bass"] or bands["mid"] or bands["high"]):
        min_iv = max(0.35, iv)
        if bands["bass"] and (beat > 0.5 or bass > 0.7):
            for e in bands["bass"]:
                if az.ok(e, bright(bass), 5, 80, min_iv, force=beat > 0.5):
                    ha.set_lights([e], bright(bass) if beat < 0.5 else max_b,
                                  hs=(0 if beat > 0.5 else 5, 0 if beat > 0.5 else 80),
                                  trans=0.0 if beat > 0.5 else 0.1)
        if bands["mid"]:
            for e in bands["mid"]:
                if az.ok(e, bright(mid), 130, 70, min_iv):
                    ha.set_lights([e], bright(mid), hs=(130, 70), trans=0.1)
        if bands["high"]:
            for e in bands["high"]:
                if az.ok(e, bright(high), 250, 65, min_iv):
                    ha.set_lights([e], bright(high), hs=(250, 65), trans=0.1)
        if mode == "spectrum" and bands["full"]:
            hue = (mid * 120 + high * 240) % 360
            for e in bands["full"]:
                if az.ok(e, bright(amp), hue, 55, min_iv):
                    ha.set_lights([e], bright(amp), hs=(hue, 55), trans=0.1)
        return

    if mode == "color_cycle":
        az.hue = (az.hue + 4) % 360
        for e in targets:
            if az.ok(e, bright(max(0.3, amp)), az.hue, 70, max(0.5, iv)):
                ha.set_lights([e], bright(max(0.3, amp)), hs=(az.hue, 70), trans=0.15)
        return

    if mode == "cinema":
        for e in targets:
            if az.ok(e, bright(0.3 + bass * 0.4), 30, 65, max(0.7, iv * 2)):
                ha.set_lights([e], bright(0.3 + bass * 0.4), hs=(30, 65), trans=0.2)
        return

    for e in targets:
        if az.ok(e, bright(max(0.3, amp)), 0, 0, max(0.4, iv)):
            ha.set_lights([e], bright(max(0.3, amp)), hs=(0, 0), trans=0.1)

def start_ff():
    url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-listen", "1", "-timeout", "0", "-i", url,
           "-vn", "-ac", str(CH), "-ar", str(SR), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    log.info("FFmpeg: %s", url)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

def read_err(proc):
    if not proc.stderr:
        return
    for line in iter(proc.stderr.readline, b""):
        if not line:
            continue
        t = line.decode(errors="replace").rstrip()
        if not t or t.startswith("size="):
            continue
        (log.warning if any(k in t.lower() for k in ("error", "fail", "invalid")) else log.info)("ffmpeg: %s", t)

class Null:
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

def reload_cfg():
    global MODE, SENS, UPDATE_MS, TRANS, MIN_B, MAX_B, BEAT_T, LIGHTS, BASS_L, MID_L, HIGH_L, ENABLED
    o = _opt()
    ENABLED = bool(o.get("enabled", True))
    MODE = str(o.get("mode") or MODE)
    SENS = float(o.get("sensitivity", SENS))
    UPDATE_MS = max(int(o.get("update_interval_ms", UPDATE_MS)), 120)
    TRANS = float(o.get("transition", TRANS))
    MIN_B = int(o.get("min_brightness", MIN_B))
    MAX_B = int(o.get("max_brightness", MAX_B))
    BEAT_T = float(o.get("beat_threshold", BEAT_T))
    LIGHTS[:] = [str(x) for x in (o.get("full_lights") or o.get("light_entities") or [])]
    BASS_L[:] = [str(x) for x in (o.get("bass_lights") or [])]
    MID_L[:] = [str(x) for x in (o.get("mid_lights") or [])]
    HIGH_L[:] = [str(x) for x in (o.get("high_lights") or [])]
    bands = dict(bass=list(BASS_L), mid=list(MID_L), high=list(HIGH_L), full=list(LIGHTS))
    log.info("Soft-reload mode=%s lights=%s bands=%s/%s/%s iv=%d", MODE, LIGHTS, BASS_L, MID_L, HIGH_L, UPDATE_MS)
    return bands

def main():
    global state
    state = WatchdogState() if _HAS_WEBUI else Null()
    if _HAS_WEBUI:
        state.set(enabled=ENABLED, rtmp_url=f"rtmp://<HA-IP>:1935/{RTMP_APP}/{RTMP_STREAM}")
        webui.RELOAD_CALLBACK = lambda: open("/tmp/larix_reload", "w").write("1")
        webui.start_server(state, port=WEBUI_PORT)
    if not TOKEN:
        log.error("no SUPERVISOR_TOKEN")
        sys.exit(1)
    if not ENABLED:
        log.info("disabled")
        while True:
            time.sleep(1)
    ha = HA()
    az = Analyzer(BEAT_T)
    bands = dict(bass=list(BASS_L), mid=list(MID_L), high=list(HIGH_L), full=list(LIGHTS))
    all_e = list(set(bands["bass"] + bands["mid"] + bands["high"] + bands["full"]))
    state.set(mode=MODE, sensitivity=SENS, bands=bands, connection_state="waiting")

    def sd(s, f):
        az.running = False
        ha.off(all_e)
        state.mark_stopped()
        sys.exit(0)

    signal.signal(signal.SIGTERM, sd)
    signal.signal(signal.SIGINT, sd)
    log.info("Active mode=%s sens=%.2f iv=%dms lights=%s", MODE, SENS, UPDATE_MS, all_e)

    while az.running:
        proc = start_ff()
        threading.Thread(target=read_err, args=(proc,), daemon=True).start()
        log.info("Waiting for Larix...")
        state.mark_waiting()
        last_up = 0.0
        last_data = time.time()
        last_log = -1
        last_cfg = 0.0
        cfg_hash = ""
        buf = bytearray()
        try:
            while az.running and proc.poll() is None:
                now = time.time()
                if now - last_cfg > 1.5:
                    last_cfg = now
                    try:
                        want = os.path.isfile("/tmp/larix_reload")
                        if want:
                            try:
                                os.remove("/tmp/larix_reload")
                            except OSError:
                                pass
                        try:
                            with open("/data/options.json", "rb") as fh:
                                h = hashlib.md5(fh.read()).hexdigest()
                        except Exception:
                            h = ""
                        if want or (h and h != cfg_hash):
                            prev = list(LIGHTS) + list(BASS_L) + list(MID_L) + list(HIGH_L)
                            bands = reload_cfg()
                            az.bt = BEAT_T
                            new = list(LIGHTS) + list(BASS_L) + list(MID_L) + list(HIGH_L)
                            if prev != new:
                                az._last.clear()
                                az._hold_bri = None
                            all_e = list(set(bands["bass"] + bands["mid"] + bands["high"] + bands["full"]))
                            state.set(mode=MODE, sensitivity=SENS, bands=bands)
                            state.log_event("Config live (kein Neustart)", "info")
                            cfg_hash = h
                        elif h:
                            cfg_hash = h
                    except Exception as e:
                        log.debug("cfg: %s", e)

                chunk = proc.stdout.read(BPC)
                if not chunk:
                    if time.time() - last_data > 4:
                        state.mark_no_signal()
                    time.sleep(0.02)
                    continue
                last_data = time.time()
                state.mark_chunk_received()
                buf.extend(chunk)
                while len(buf) >= BPC:
                    frame = bytes(buf[:BPC])
                    del buf[:BPC]
                    feat = az.process(frame, SENS)
                    state.set_features(feat)
                    now = time.time()
                    if (now - last_up) * 1000 >= UPDATE_MS:
                        map_lights(ha, az, feat, bands, MODE, MIN_B, MAX_B, TRANS, UPDATE_MS / 1000.0)
                        state.mark_light_update()
                        sec = int(now)
                        if sec != last_log and sec % 2 == 0:
                            try:
                                peak = int(np.max(np.abs(np.frombuffer(frame, dtype=np.int16))))
                            except Exception:
                                peak = 0
                            log.info("audio amp=%.3f bass=%.3f mid=%.3f high=%.3f beat=%.0f peak=%d mode=%s",
                                     feat["amplitude"], feat["bass"], feat["mid"], feat["high"],
                                     feat["beat"], peak, MODE)
                            last_log = sec
                        last_up = now
        except Exception as e:
            log.error("loop: %s", e)
            state.mark_error(str(e))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if az.running:
            log.warning("FFmpeg exited – restart 3s")
            state.mark_ffmpeg_restart()
            time.sleep(3)

if __name__ == "__main__":
    main()
