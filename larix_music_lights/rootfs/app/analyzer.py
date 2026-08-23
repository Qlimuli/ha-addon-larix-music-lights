#!/usr/bin/env python3
"""Larix Music Reactive Lights v1.5.0 – fire-and-forget + soft-reload"""
import os, sys, json, time, logging, subprocess, threading, signal, colorsys
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
            except Exception as e: print("WARNING options", e, flush=True)
    return {}

O = _opt()
ENABLED = bool(O.get("enabled", True))
MODE = str(O.get("mode") or "auto")
SENS = float(O.get("sensitivity", 1.2))
UPDATE_MS = max(int(O.get("update_interval_ms", 250)), 180)
TRANS = float(O.get("transition", 0.12))
MIN_B = int(O.get("min_brightness", 30))
MAX_B = int(O.get("max_brightness", 255))
BEAT_T = float(O.get("beat_threshold", 0.45))
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
log.info("Config: lights=%s bass=%s mid=%s high=%s interval=%dms", LIGHTS, BASS_L, MID_L, HIGH_L, UPDATE_MS)
state = None

class HA:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        self._lock = threading.Lock()
        self._n = 0
        self._max = 3

    def _req(self, method, path, **kw):
        try:
            r = self.s.request(method, f"{HA_URL}{path}", timeout=2.0, **kw)
            r.raise_for_status()
            return r.json() if r.content else True
        except Exception as e:
            log.debug("HA %s %s: %s", method, path, e)
            return None

    def fire(self, method, path, **kw):
        with self._lock:
            if self._n >= self._max: return
            self._n += 1
        def w():
            try: self._req(method, path, **kw)
            finally:
                with self._lock: self._n = max(0, self._n - 1)
        threading.Thread(target=w, daemon=True).start()

    def set_lights(self, eids, bri, hs=None, trans=0.12):
        if not eids: return
        bri = max(1, min(255, int(bri)))
        p = {"entity_id": list(eids), "brightness": bri, "transition": max(0.0, float(trans))}
        if hs is not None:
            try:
                h = (float(hs[0]) % 360) / 360.0
                s = max(0.0, min(1.0, float(hs[1]) / 100.0))
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                def lin(c): return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
                r2, g2, b2 = lin(r), lin(g), lin(b)
                X = r2*0.4124+g2*0.3576+b2*0.1805
                Y = r2*0.2126+g2*0.7152+b2*0.0722
                Z = r2*0.0193+g2*0.1192+b2*0.9505
                t = X+Y+Z
                if t > 1e-6: p["xy_color"] = [round(X/t, 4), round(Y/t, 4)]
            except Exception:
                p["hs_color"] = [float(hs[0]) % 360, float(hs[1])]
        self.fire("POST", "/services/light/turn_on", json=p)

    def off(self, eids, trans=1.5):
        if eids: self.fire("POST", "/services/light/turn_off", json={"entity_id": list(eids), "transition": trans})

class Analyzer:
    def __init__(self, bt):
        self.bh = deque(maxlen=40); self.eh = deque(maxlen=40); self.ph = deque(maxlen=100)
        self.last_beat = 0.0; self.hue = 0.0; self.bt = bt; self.running = True
        self._last = {}; self._bm = self._mm = self._hm = 1e-6

    def process(self, pcm, sens):
        if len(pcm) < 1024: return dict(amplitude=0,bass=0,mid=0,high=0,beat=0)
        if len(pcm)%2: pcm = pcm[:-1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if not samples.size: return dict(amplitude=0,bass=0,mid=0,high=0,beat=0)
        peak = float(np.max(np.abs(samples))); samples /= 32768.0
        w = samples * np.hanning(len(samples))
        fft = np.abs(np.fft.rfft(w)); freqs = np.fft.rfftfreq(len(w), 1.0/SR)
        def band(lo,hi):
            m = (freqs>=lo)&(freqs<hi)
            return float(np.mean(fft[m])) if np.any(m) else 0.0
        br, mr, hr = band(20,180), band(180,2000), band(2000,9000)
        rms = float(np.sqrt(np.mean(samples**2)))
        self.ph.append(peak); pref = max(max(self.ph), 500.0)
        amp = min(1.0, (peak/pref)*sens); amp = max(amp, min(1.0, rms*22*sens))
        d = 0.985
        self._bm = max(br, self._bm*d); self._mm = max(mr, self._mm*d); self._hm = max(hr, self._hm*d)
        bass = min(1.0, (br/(self._bm+1e-12))**0.75 * (0.85+0.25*sens))
        mid  = min(1.0, (mr/(self._mm+1e-12))**0.75 * (0.85+0.25*sens))
        high = min(1.0, (hr/(self._hm+1e-12))**0.75 * (0.85+0.25*sens))
        self.bh.append(bass); self.eh.append(amp)
        beat = 0.0; now = time.time()
        if len(self.eh) >= 8 and (now - self.last_beat) > 0.22:
            ea = sum(self.eh)/len(self.eh); ba = sum(self.bh)/len(self.bh)
            if (amp > ea*(1.15+self.bt*0.5) and amp > 0.18) or (bass > ba*1.35 and bass > 0.55 and amp > 0.12) or (peak > pref*0.55 and amp > 0.30):
                beat = 1.0; self.last_beat = now
        return dict(amplitude=amp, bass=bass, mid=mid, high=high, beat=beat)

    def ok(self, eid, bri, hue, sat, iv, force=False):
        now = time.time(); prev = self._last.get(eid)
        if force or prev is None:
            self._last[eid] = (bri,hue,sat,now); return True
        pb,ph,ps,pt = prev
        if (now-pt) < iv: return False
        if abs(bri-pb)<20 and abs(hue-ph)<20 and abs(sat-ps)<15: return False
        self._last[eid] = (bri,hue,sat,now); return True

def map_lights(ha, az, f, bands, mode, min_b, max_b, trans, iv):
    amp,bass,mid,high,beat = f["amplitude"],f["bass"],f["mid"],f["high"],f["beat"]
    if amp < 0.03 and bass < 0.05: return
    def bright(v): return max(min_b, min(max_b, int(min_b+(max_b-min_b)*v)))
    def send(ents, bri, hue, sat, force=False, t=None):
        if not ents: return
        n = max(1, len(ents)); interval = iv * (1.0 + 0.4*(n-1))
        to = [e for e in ents if az.ok(e, bri, hue, sat, interval, force)]
        if to: ha.set_lights(to, bri, hs=(hue,sat), trans=trans if t is None else t)
    has = bool(bands["bass"] or bands["mid"] or bands["high"])
    targets = bands["full"] or bands["bass"] or bands["mid"] or bands["high"]
    if mode == "spectrum" or (has and mode in ("auto","pulse")):
        if bands["bass"]: send(bands["bass"], bright(bass), 5, 80, force=beat>0.5, t=min(trans,0.1))
        if bands["mid"]: send(bands["mid"], bright(mid), 130, 70, t=min(trans,0.12))
        if bands["high"]: send(bands["high"], bright(high), 250, 65, t=min(trans,0.12))
        if mode == "spectrum":
            if bands["full"]:
                hue = (mid*120+high*240)%360; send(bands["full"], bright(amp), hue, 60)
            return
        if has and not bands["full"]: return
    if mode in ("auto","pulse"):
        energy = min(1.0, amp*0.65+bass*0.40+mid*0.25+high*0.12)
        level = max(0.28, min(1.0, (energy**0.5)*1.2))
        tot = bass+mid+high+1e-6
        hue = (bass/tot*8 + mid/tot*145 + high/tot*255)%360
        sat = 35 + min(55, bass*30+high*30)
        if beat>0.5 or (amp>=0.45 and bass>=0.55) or amp>=0.70:
            send(targets, max_b, 0, 0, force=True, t=0.0)
        else:
            send(targets, bright(level), hue, sat, t=min(trans,0.12))
        return
    if mode == "color_cycle":
        az.hue = (az.hue + 2 + bass*8)%360
        send(targets, bright(max(0.35,amp)), az.hue, 80); return
    if mode == "cinema":
        send(targets, bright(0.35+bass*0.5), 30, 70, t=0.25); return
    send(targets, bright(max(0.35,amp)), 0, 0)

def start_ff():
    url = f"rtmp://0.0.0.0:1935/{RTMP_APP}/{RTMP_STREAM}"
    cmd = ["ffmpeg","-hide_banner","-loglevel","info","-listen","1","-timeout","0","-i",url,
           "-vn","-ac",str(CH),"-ar",str(SR),"-f","s16le","-acodec","pcm_s16le","pipe:1"]
    log.info("FFmpeg: %s", url)
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

def read_err(proc):
    if not proc.stderr: return
    for line in iter(proc.stderr.readline, b""):
        if not line: continue
        t = line.decode(errors="replace").rstrip()
        if not t or t.startswith("size="): continue
        (log.warning if any(k in t.lower() for k in ("error","fail","invalid")) else log.info)("ffmpeg: %s", t)

class Null:
    def set(self,**k): pass
    def log_event(self,*a,**k): pass
    def mark_error(self,*a,**k): pass
    def mark_stopped(self,*a,**k): pass
    def mark_waiting(self,*a,**k): pass
    def mark_no_signal(self,*a,**k): pass
    def mark_chunk_received(self,*a,**k): pass
    def mark_light_update(self,*a,**k): pass
    def mark_ffmpeg_restart(self,*a,**k): pass
    def set_features(self,*a,**k): pass

def reload_cfg():
    global MODE, SENS, UPDATE_MS, TRANS, MIN_B, MAX_B, BEAT_T, LIGHTS, BASS_L, MID_L, HIGH_L, ENABLED
    o = _opt()
    ENABLED = bool(o.get("enabled", True))
    MODE = str(o.get("mode") or MODE)
    SENS = float(o.get("sensitivity", SENS))
    UPDATE_MS = max(int(o.get("update_interval_ms", UPDATE_MS)), 180)
    TRANS = float(o.get("transition", TRANS))
    MIN_B = int(o.get("min_brightness", MIN_B))
    MAX_B = int(o.get("max_brightness", MAX_B))
    BEAT_T = float(o.get("beat_threshold", BEAT_T))
    LIGHTS[:] = [str(x) for x in (o.get("full_lights") or o.get("light_entities") or [])]
    BASS_L[:] = [str(x) for x in (o.get("bass_lights") or [])]
    MID_L[:] = [str(x) for x in (o.get("mid_lights") or [])]
    HIGH_L[:] = [str(x) for x in (o.get("high_lights") or [])]
    bands = dict(bass=list(BASS_L), mid=list(MID_L), high=list(HIGH_L), full=list(LIGHTS))
    log.info("Soft-reload mode=%s lights=%s bands=%s/%s/%s", MODE, LIGHTS, BASS_L, MID_L, HIGH_L)
    return bands

def main():
    global state
    state = WatchdogState() if _HAS_WEBUI else Null()
    if _HAS_WEBUI:
        state.set(enabled=ENABLED, rtmp_url=f"rtmp://<HA-IP>:1935/{RTMP_APP}/{RTMP_STREAM}")
        webui.RELOAD_CALLBACK = lambda: open("/tmp/larix_reload","w").write("1")
        webui.start_server(state, port=WEBUI_PORT)
    if not TOKEN: log.error("no SUPERVISOR_TOKEN"); sys.exit(1)
    if not ENABLED:
        log.info("disabled");
        while True: time.sleep(1)
    ha = HA(); az = Analyzer(BEAT_T)
    bands = dict(bass=list(BASS_L), mid=list(MID_L), high=list(HIGH_L), full=list(LIGHTS))
    all_e = list(set(bands["bass"]+bands["mid"]+bands["high"]+bands["full"]))
    state.set(mode=MODE, sensitivity=SENS, bands=bands, connection_state="waiting")
    def sd(s,f):
        az.running=False; ha.off(all_e); state.mark_stopped(); sys.exit(0)
    signal.signal(signal.SIGTERM, sd); signal.signal(signal.SIGINT, sd)
    log.info("Active mode=%s sens=%.2f iv=%dms lights=%s", MODE, SENS, UPDATE_MS, all_e)
    while az.running:
        proc = start_ff(); threading.Thread(target=read_err, args=(proc,), daemon=True).start()
        log.info("Waiting for Larix..."); state.mark_waiting()
        last_up = 0.0; last_data = time.time(); last_log = -1; last_cfg = 0.0; cfg_mt = 0.0
        buf = bytearray()
        try:
            while az.running and proc.poll() is None:
                now = time.time()
                if now - last_cfg > 1.2:
                    last_cfg = now
                    try:
                        mt = os.path.getmtime("/data/options.json")
                        if (cfg_mt and mt > cfg_mt) or os.path.isfile("/tmp/larix_reload"):
                            if os.path.isfile("/tmp/larix_reload"):
                                try: os.remove("/tmp/larix_reload")
                                except OSError: pass
                            bands = reload_cfg(); az.bt = BEAT_T; az._last.clear()
                            all_e = list(set(bands["bass"]+bands["mid"]+bands["high"]+bands["full"]))
                            state.set(mode=MODE, sensitivity=SENS, bands=bands)
                            state.log_event("Config live (kein Neustart)", "info")
                        cfg_mt = mt
                    except Exception as e: log.debug("cfg: %s", e)
                chunk = proc.stdout.read(BPC)
                if not chunk:
                    if time.time()-last_data > 4: state.mark_no_signal()
                    time.sleep(0.02); continue
                last_data = time.time(); state.mark_chunk_received(); buf.extend(chunk)
                while len(buf) >= BPC:
                    frame = bytes(buf[:BPC]); del buf[:BPC]
                    feat = az.process(frame, SENS); state.set_features(feat)
                    now = time.time()
                    if (now-last_up)*1000 >= UPDATE_MS:
                        map_lights(ha, az, feat, bands, MODE, MIN_B, MAX_B, TRANS, UPDATE_MS/1000.0)
                        state.mark_light_update()
                        sec = int(now)
                        if sec != last_log and sec % 2 == 0:
                            try: peak = int(np.max(np.abs(np.frombuffer(frame, dtype=np.int16))))
                            except Exception: peak = 0
                            log.info("audio amp=%.3f bass=%.3f mid=%.3f high=%.3f beat=%.0f peak=%d mode=%s",
                                     feat["amplitude"], feat["bass"], feat["mid"], feat["high"], feat["beat"], peak, MODE)
                            last_log = sec
                        last_up = now
        except Exception as e:
            log.error("loop: %s", e); state.mark_error(str(e))
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill()
        if az.running:
            log.warning("FFmpeg exited – restart 3s"); state.mark_ffmpeg_restart(); time.sleep(3)

if __name__ == "__main__":
    main()
