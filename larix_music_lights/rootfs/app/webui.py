#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Watchdog Web UI
Lightweight stdlib-only HTTP server that serves a live status dashboard.
Designed to run behind Home Assistant Ingress, so all asset references in
the HTML are relative (no leading "/") and no external CDN is used.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from watchdog_state import WatchdogState

log = logging.getLogger("larix-music.webui")

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Larix Music Lights - Watchdog</title>
<style>
  :root {
    --bg: #0f1115;
    --card: #181b21;
    --card-border: #262b34;
    --text: #e7e9ee;
    --text-dim: #8b93a3;
    --bass: #ff4d5e;
    --mid: #34d17c;
    --high: #4da3ff;
    --amp: #b98bff;
    --green: #34d17c;
    --yellow: #f0b93a;
    --orange: #ff9a3d;
    --red: #ff4d5e;
    --grey: #5b6472;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
  }
  .wrap { max-width: 760px; margin: 0 auto; }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    flex-wrap: wrap;
    gap: 8px;
  }
  h1 {
    font-size: 18px;
    margin: 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h1 .icon { font-size: 22px; }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    border: 1px solid var(--card-border);
    background: var(--card);
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--grey);
    box-shadow: 0 0 0 0 rgba(0,0,0,0);
  }
  .dot.pulse { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
    70%  { box-shadow: 0 0 0 7px transparent; opacity: 0.6; }
    100% { box-shadow: 0 0 0 0 transparent; opacity: 1; }
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 12px 14px;
  }
  .card .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
    margin-bottom: 4px;
  }
  .card .value {
    font-size: 17px;
    font-weight: 600;
    word-break: break-word;
  }
  .card .value.small { font-size: 13px; font-weight: 500; color: var(--text-dim); }

  .panel {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
  }
  .panel h2 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
    margin: 0 0 14px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .beat-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--grey);
    transition: transform 0.05s ease-out, background 0.05s ease-out;
  }
  .beat-dot.hit {
    background: #ffffff;
    transform: scale(1.6);
  }

  .meter { margin-bottom: 12px; }
  .meter:last-child { margin-bottom: 0; }
  .meter-head {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 5px;
  }
  .meter-track {
    height: 10px;
    border-radius: 6px;
    background: #0b0d11;
    overflow: hidden;
    border: 1px solid var(--card-border);
  }
  .meter-fill {
    height: 100%;
    width: 0%;
    border-radius: 6px;
    transition: width 90ms linear;
  }
  .meter-fill.bass { background: linear-gradient(90deg, #7a0f1c, var(--bass)); }
  .meter-fill.mid  { background: linear-gradient(90deg, #0f7a3c, var(--mid)); }
  .meter-fill.high { background: linear-gradient(90deg, #0f4a7a, var(--high)); }
  .meter-fill.amp  { background: linear-gradient(90deg, #4a2380, var(--amp)); }

  .events {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 240px;
    overflow-y: auto;
  }
  .events li {
    display: flex;
    gap: 10px;
    padding: 7px 0;
    border-bottom: 1px solid var(--card-border);
    font-size: 13px;
  }
  .events li:last-child { border-bottom: none; }
  .events .time { color: var(--text-dim); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .events .lvl {
    width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; flex: none;
  }
  .lvl-info { background: var(--grey); }
  .lvl-success { background: var(--green); }
  .lvl-warning { background: var(--yellow); }
  .lvl-error { background: var(--red); }
  .empty { color: var(--text-dim); font-size: 13px; }

  .offline-banner {
    display: none;
    background: #3a1418;
    border: 1px solid var(--red);
    color: #ffb4bc;
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
    font-size: 13px;
  }
  .offline-banner.show { display: block; }

  footer {
    text-align: center;
    color: var(--text-dim);
    font-size: 11px;
    margin-top: 8px;
  }
</style>
</head>
<body>
<div class="wrap">

  <div class="offline-banner" id="offlineBanner">
    Verbindung zum Add-on verloren - versuche erneut zu verbinden...
  </div>

  <header>
    <h1><span class="icon">🎧</span> Larix Music Lights</h1>
    <div class="badge" id="statusBadge">
      <span class="dot" id="statusDot"></span>
      <span id="statusText">Lade...</span>
    </div>
  </header>

  <div class="grid">
    <div class="card">
      <div class="label">Profil</div>
      <div class="value" id="profileName">-</div>
      <div class="value small" id="profileRoom"></div>
    </div>
    <div class="card">
      <div class="label">Modus</div>
      <div class="value" id="modeVal">-</div>
    </div>
    <div class="card">
      <div class="label">Laufzeit</div>
      <div class="value" id="uptimeVal">-</div>
    </div>
    <div class="card">
      <div class="label">FFmpeg-Neustarts</div>
      <div class="value" id="restartsVal">-</div>
    </div>
    <div class="card">
      <div class="label">Verbindungen gesamt</div>
      <div class="value" id="connectionsVal">-</div>
    </div>
    <div class="card">
      <div class="label">Letzter Fehler</div>
      <div class="value small" id="errorVal">keiner</div>
    </div>
  </div>

  <div class="panel">
    <h2>
      Live-Pegel
      <span class="beat-dot" id="beatDot" title="Beat erkannt"></span>
    </h2>
    <div class="meter">
      <div class="meter-head"><span>Bass</span><span id="bassPct">0%</span></div>
      <div class="meter-track"><div class="meter-fill bass" id="bassBar"></div></div>
    </div>
    <div class="meter">
      <div class="meter-head"><span>Mitten</span><span id="midPct">0%</span></div>
      <div class="meter-track"><div class="meter-fill mid" id="midBar"></div></div>
    </div>
    <div class="meter">
      <div class="meter-head"><span>Höhen</span><span id="highPct">0%</span></div>
      <div class="meter-track"><div class="meter-fill high" id="highBar"></div></div>
    </div>
    <div class="meter">
      <div class="meter-head"><span>Amplitude</span><span id="ampPct">0%</span></div>
      <div class="meter-track"><div class="meter-fill amp" id="ampBar"></div></div>
    </div>
  </div>

  <div class="panel">
    <h2>RTMP-Eingang</h2>
    <div class="value small" id="rtmpVal" style="font-family: monospace; font-size: 13px;">-</div>
  </div>

  <div class="panel">
    <h2>Ereignisse</h2>
    <ul class="events" id="eventsList">
      <li class="empty">Noch keine Ereignisse.</li>
    </ul>
  </div>

  <footer>Watchdog GUI &middot; aktualisiert automatisch alle 800&nbsp;ms</footer>
</div>

<script>
const STATE_LABELS = {
  starting:  { text: "Startet...",            color: "var(--grey)"   },
  disabled:  { text: "Deaktiviert",           color: "var(--grey)"   },
  waiting:   { text: "Warte auf Larix",       color: "var(--yellow)" },
  connected: { text: "Verbunden",             color: "var(--green)"  },
  no_signal: { text: "Kein Signal",           color: "var(--orange)" },
  stopped:   { text: "Gestoppt",              color: "var(--red)"    },
};

function fmtDuration(seconds) {
  if (seconds == null) return "-";
  seconds = Math.floor(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtAge(seconds) {
  if (seconds == null) return "nie";
  if (seconds < 1) return "gerade eben";
  return `vor ${Math.floor(seconds)}s`;
}

function pct(v) {
  return Math.round(Math.max(0, Math.min(1, v || 0)) * 100);
}

let lastBeatAt = null;

function render(data) {
  const st = STATE_LABELS[data.connection_state] || { text: data.connection_state, color: "var(--grey)" };
  const dot = document.getElementById("statusDot");
  const badge = document.getElementById("statusBadge");
  document.getElementById("statusText").textContent = st.text;
  dot.style.background = st.color;
  dot.style.color = st.color;
  badge.style.borderColor = st.color;
  dot.classList.toggle("pulse", data.connection_state === "connected" || data.connection_state === "waiting");

  document.getElementById("profileName").textContent = data.profile_name || "(legacy)";
  document.getElementById("profileRoom").textContent = data.room || "";
  document.getElementById("modeVal").textContent = data.mode || "-";
  document.getElementById("uptimeVal").textContent = fmtDuration(data.uptime_s);
  document.getElementById("restartsVal").textContent = data.ffmpeg_restarts;
  document.getElementById("connectionsVal").textContent = data.stream_connections;
  document.getElementById("errorVal").textContent = data.last_error
    ? `${data.last_error} (${fmtAge(data.last_error_age_s)})`
    : "keiner";
  document.getElementById("rtmpVal").textContent = data.rtmp_url || "-";

  const f = data.features || {};
  document.getElementById("bassPct").textContent = pct(f.bass) + "%";
  document.getElementById("bassBar").style.width = pct(f.bass) + "%";
  document.getElementById("midPct").textContent = pct(f.mid) + "%";
  document.getElementById("midBar").style.width = pct(f.mid) + "%";
  document.getElementById("highPct").textContent = pct(f.high) + "%";
  document.getElementById("highBar").style.width = pct(f.high) + "%";
  document.getElementById("ampPct").textContent = pct(f.amplitude) + "%";
  document.getElementById("ampBar").style.width = pct(f.amplitude) + "%";

  const beatDot = document.getElementById("beatDot");
  if (data.last_beat_age_s != null && data.last_beat_age_s < 0.3 && lastBeatAt !== data.last_beat_age_s) {
    beatDot.classList.add("hit");
    setTimeout(() => beatDot.classList.remove("hit"), 120);
  }
  lastBeatAt = data.last_beat_age_s;

  const list = document.getElementById("eventsList");
  const events = data.events || [];
  if (events.length === 0) {
    list.innerHTML = '<li class="empty">Noch keine Ereignisse.</li>';
  } else {
    list.innerHTML = events.map(ev => {
      const d = new Date(ev.t * 1000);
      const time = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      return `<li><span class="lvl lvl-${ev.level}"></span><span class="time">${time}</span><span>${ev.message}</span></li>`;
    }).join("");
  }
}

async function poll() {
  try {
    const res = await fetch("api/status", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    document.getElementById("offlineBanner").classList.remove("show");
    render(data);
  } catch (e) {
    document.getElementById("offlineBanner").classList.add("show");
  } finally {
    setTimeout(poll, 800);
  }
}

poll();
</script>
</body>
</html>
"""


class WatchdogRequestHandler(BaseHTTPRequestHandler):
    # Set by start_server() before the server starts accepting connections.
    state: WatchdogState = None  # type: ignore[assignment]

    server_version = "LarixMusicWatchdog/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - stdlib signature
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        path = urlparse(self.path).path

        if path in ("", "/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML.encode("utf-8"))
            return

        if path in ("/api/status", "api/status"):
            data = json.dumps(self.state.snapshot()).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", data)
            return

        self._send(404, "text/plain; charset=utf-8", b"Not found")


def start_server(state: WatchdogState, host: str = "0.0.0.0", port: int = 8099) -> ThreadingHTTPServer:
    """Start the Watchdog GUI HTTP server in a background thread."""
    WatchdogRequestHandler.state = state
    server = ThreadingHTTPServer((host, port), WatchdogRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="watchdog-webui", daemon=True)
    thread.start()
    log.info("Watchdog GUI listening on %s:%s", host, port)
    return server
