#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Web UI
Lightweight stdlib-only HTTP server that serves a live status dashboard
AND a full settings editor, so the entire add-on configuration (mode,
thresholds, selected lights, room profiles, ...) can be managed from the
Ingress panel instead of the YAML-based Supervisor "Configuration" tab.

Designed to run behind Home Assistant Ingress, so all asset references in
the HTML are relative (no leading "/") and no external CDN is used.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import configapi
from watchdog_state import WatchdogState

log = logging.getLogger("larix-music.webui")

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Larix Music Lights</title>
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
    --accent: #5b8dff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 16px;
  }
  .wrap { max-width: 860px; margin: 0 auto; }
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

  .tabs {
    display: flex;
    gap: 6px;
    margin-bottom: 16px;
    border-bottom: 1px solid var(--card-border);
  }
  .tab-btn {
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 14px;
    font-weight: 600;
    padding: 10px 14px;
    cursor: pointer;
    border-bottom: 2px solid transparent;
  }
  .tab-btn.active { color: var(--text); border-bottom-color: var(--accent); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

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

  /* ---- Settings form ---- */
  .field { margin-bottom: 12px; }
  .field label {
    display: block;
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 4px;
  }
  .field .hint { font-size: 11px; color: var(--text-dim); margin-top: 3px; }
  .field-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
  input[type="text"], input[type="number"], select, textarea {
    width: 100%;
    background: #0b0d11;
    border: 1px solid var(--card-border);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 14px;
  }
  input[type="checkbox"] { accent-color: var(--accent); }

  .btn {
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 999px;
    padding: 11px 20px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn:hover { filter: brightness(1.08); }
  .btn:active { transform: scale(0.98); }
  .btn.secondary { background: #262b34; color: var(--text); }
  .btn.danger { background: #4a1c22; color: #ffb4bc; }
  .btn.small { padding: 6px 12px; font-size: 12px; }

  /* ---- Nicer panel headers with an icon bubble ---- */
  .panel h2 .h2-title { display: flex; align-items: center; gap: 8px; }
  .panel h2 .h2-icon {
    width: 24px; height: 24px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 13px;
    background: rgba(91, 141, 255, 0.15);
  }

  /* ---- Toggle switch (replaces the plain checkbox for "enabled") ---- */
  .switch { position: relative; display: inline-block; width: 46px; height: 26px; flex: none; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch .track {
    position: absolute; inset: 0; cursor: pointer;
    background: #2b2f38; border-radius: 999px; transition: background 0.15s ease;
  }
  .switch .track::before {
    content: ""; position: absolute; height: 20px; width: 20px; left: 3px; bottom: 3px;
    background: #fff; border-radius: 50%; transition: transform 0.15s ease;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  }
  .switch input:checked + .track { background: var(--accent); }
  .switch input:checked + .track::before { transform: translateX(20px); }
  .toggle-row { display: flex; align-items: center; gap: 12px; }
  .toggle-row .toggle-label { font-size: 14px; font-weight: 600; }
  .toggle-row .toggle-sub { font-size: 12px; color: var(--text-dim); }

  /* ---- Dropdown styled as a single row (icon + value + chevron), like a picker row ---- */
  .select-row {
    display: flex; align-items: center; gap: 10px;
    background: #0b0d11; border: 1px solid var(--card-border);
    border-radius: 10px; padding: 2px 12px;
  }
  .select-row .select-icon { font-size: 17px; flex: none; }
  .select-row select {
    border: none; background: transparent; padding: 10px 4px; appearance: none;
    -webkit-appearance: none;
  }
  .select-row::after {
    content: "";
  }

  /* ---- Icon + slider + value rows, à la "Microphone sensitivity" sliders ---- */
  .slider-field { margin-bottom: 18px; }
  .slider-field .slider-label {
    display: flex; justify-content: space-between; align-items: baseline;
    font-size: 13px; color: var(--text); margin-bottom: 8px;
  }
  .slider-field .slider-label .slider-val {
    font-size: 12px; font-weight: 700; color: var(--accent);
    background: rgba(91, 141, 255, 0.12);
    border-radius: 999px; padding: 2px 10px;
  }
  .slider-row { display: flex; align-items: center; gap: 12px; }
  .slider-row .slider-icon {
    width: 30px; height: 30px; border-radius: 50%;
    background: #0b0d11; border: 1px solid var(--card-border);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex: none;
  }
  input[type="range"] {
    -webkit-appearance: none;
    appearance: none;
    width: 100%;
    height: 6px;
    border-radius: 999px;
    background: #262b34;
    outline: none;
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 20px; height: 20px;
    border-radius: 50%;
    background: var(--accent);
    border: 3px solid #10131a;
    box-shadow: 0 1px 4px rgba(0,0,0,0.5);
    cursor: pointer;
    margin-top: -1px;
  }
  input[type="range"]::-moz-range-thumb {
    width: 20px; height: 20px;
    border-radius: 50%;
    background: var(--accent);
    border: 3px solid #10131a;
    cursor: pointer;
  }
  input[type="range"]::-moz-range-track {
    height: 6px; border-radius: 999px; background: #262b34;
  }
  input[type="range"].hue-slider {
    background: linear-gradient(90deg, #ff3b30, #ffcc00, #34c759, #34d1c3, #5b8dff, #af52de, #ff3b30);
  }

  .picker {
    border: 1px solid var(--card-border);
    border-radius: 8px;
    background: #0b0d11;
    overflow: hidden;
  }
  .picker input[type="text"] {
    border: none;
    border-bottom: 1px solid var(--card-border);
    border-radius: 0;
  }
  .picker-list {
    max-height: 190px;
    overflow-y: auto;
    padding: 6px 4px;
  }
  .picker-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border-radius: 6px;
    font-size: 13px;
  }
  .picker-row:hover { background: #161a21; }
  .picker-row:has(input:checked) { background: rgba(91, 141, 255, 0.12); }
  .picker-row .entity-id { color: var(--text-dim); font-size: 11px; margin-left: auto; white-space: nowrap; }
  .picker-count { font-size: 11px; color: var(--text-dim); padding: 4px 8px 6px; }

  .profile-card {
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 14px;
    background: #12151b;
  }
  .profile-card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .profile-card-head input[type="text"] { font-weight: 600; font-size: 14px; }
  .band-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 10px; }
  .band-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 5px; }
  .band-label.bass { color: var(--bass); }
  .band-label.mid  { color: var(--mid); }
  .band-label.high { color: var(--high); }
  .band-label.full { color: var(--amp); }

  .save-bar {
    position: sticky;
    bottom: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 12px 16px;
    margin-top: 4px;
  }
  .save-status { font-size: 13px; color: var(--text-dim); }
  .save-status.ok { color: var(--green); }
  .save-status.err { color: var(--red); }
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

  <div class="tabs">
    <button class="tab-btn active" data-tab="status" onclick="switchTab('status')">Status</button>
    <button class="tab-btn" data-tab="settings" onclick="switchTab('settings')">Einstellungen</button>
  </div>

  <!-- ============================= STATUS TAB ============================= -->
  <div class="tab-panel active" id="tab-status">

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

  <!-- ============================ SETTINGS TAB ============================= -->
  <div class="tab-panel" id="tab-settings">

    <div class="panel">
      <h2><span class="h2-title"><span class="h2-icon">⚙️</span>Allgemein</span></h2>
      <div class="toggle-row field">
        <label class="switch">
          <input type="checkbox" id="cfg_enabled">
          <span class="track"></span>
        </label>
        <div>
          <div class="toggle-label">Add-on aktiviert</div>
          <div class="toggle-sub">Lichtsteuerung ein-/ausschalten</div>
        </div>
      </div>
      <div class="field-row">
        <div class="field">
          <label for="cfg_active_profile">Aktives Profil</label>
          <div class="select-row"><span class="select-icon">🏠</span><select id="cfg_active_profile"></select></div>
          <div class="hint">Leer = Legacy-Modus (Licht-/Bereichsauswahl unten).</div>
        </div>
        <div class="field">
          <label for="cfg_mode">Modus</label>
          <div class="select-row"><span class="select-icon">🎛️</span><select id="cfg_mode">
            <option value="pulse">Pulse</option>
            <option value="spectrum">Spectrum</option>
            <option value="color_cycle">Color Cycle</option>
            <option value="brightness">Brightness</option>
            <option value="cinema">Cinema</option>
          </select></div>
        </div>
        <div class="field">
          <label for="cfg_color_mode">Farbmodus</label>
          <div class="select-row"><span class="select-icon">🎨</span><select id="cfg_color_mode">
            <option value="spectrum">Spectrum</option>
            <option value="fixed">Fest</option>
            <option value="rainbow">Regenbogen</option>
          </select></div>
        </div>
        <div class="field">
          <label for="cfg_log_level">Log-Level</label>
          <div class="select-row"><span class="select-icon">📋</span><select id="cfg_log_level">
            <option value="debug">Debug</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select></div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h2><span class="h2-title"><span class="h2-icon">🎚️</span>Empfindlichkeit &amp; Timing</span></h2>

      <div class="slider-field">
        <div class="slider-label"><span>Empfindlichkeit</span><span class="slider-val" id="val_cfg_sensitivity">0.7x</span></div>
        <div class="slider-row">
          <div class="slider-icon">🎤</div>
          <input type="range" id="cfg_sensitivity" min="0.1" max="2.0" step="0.05">
        </div>
      </div>

      <div class="slider-field">
        <div class="slider-label"><span>Beat-Schwelle</span><span class="slider-val" id="val_cfg_beat_threshold">0.55</span></div>
        <div class="slider-row">
          <div class="slider-icon">🥁</div>
          <input type="range" id="cfg_beat_threshold" min="0.1" max="1.0" step="0.05">
        </div>
      </div>

      <div class="slider-field">
        <div class="slider-label"><span>Basis-Farbton</span><span class="slider-val" id="val_cfg_base_hue">0°</span></div>
        <div class="slider-row">
          <div class="slider-icon">🖌️</div>
          <input type="range" class="hue-slider" id="cfg_base_hue" min="0" max="360" step="1">
        </div>
      </div>

      <div class="field-row">
        <div class="slider-field" style="margin-bottom:0;">
          <div class="slider-label"><span>Min. Helligkeit</span><span class="slider-val" id="val_cfg_min_brightness">10</span></div>
          <div class="slider-row">
            <div class="slider-icon">🔅</div>
            <input type="range" id="cfg_min_brightness" min="1" max="255" step="1">
          </div>
        </div>
        <div class="slider-field" style="margin-bottom:0;">
          <div class="slider-label"><span>Max. Helligkeit</span><span class="slider-val" id="val_cfg_max_brightness">255</span></div>
          <div class="slider-row">
            <div class="slider-icon">🔆</div>
            <input type="range" id="cfg_max_brightness" min="1" max="255" step="1">
          </div>
        </div>
      </div>

      <div class="field-row" style="margin-top: 18px;">
        <div class="slider-field" style="margin-bottom:0;">
          <div class="slider-label"><span>Übergang</span><span class="slider-val" id="val_cfg_transition">0.15 s</span></div>
          <div class="slider-row">
            <div class="slider-icon">🌊</div>
            <input type="range" id="cfg_transition" min="0" max="2.0" step="0.05">
          </div>
        </div>
        <div class="slider-field" style="margin-bottom:0;">
          <div class="slider-label"><span>Update-Intervall</span><span class="slider-val" id="val_cfg_update_interval_ms">80 ms</span></div>
          <div class="slider-row">
            <div class="slider-icon">⚡</div>
            <input type="range" id="cfg_update_interval_ms" min="30" max="500" step="10">
          </div>
        </div>
        <div class="slider-field" style="margin-bottom:0;">
          <div class="slider-label"><span>Stille-Timeout</span><span class="slider-val" id="val_cfg_silence_timeout_s">8 s</span></div>
          <div class="slider-row">
            <div class="slider-icon">🌙</div>
            <input type="range" id="cfg_silence_timeout_s" min="2" max="60" step="1">
          </div>
        </div>
      </div>
    </div>

    <div class="panel">
      <h2><span class="h2-title"><span class="h2-icon">📡</span>RTMP-Eingang</span></h2>
      <div class="field-row">
        <div class="field">
          <label for="cfg_rtmp_app">RTMP App</label>
          <input type="text" id="cfg_rtmp_app">
        </div>
        <div class="field">
          <label for="cfg_rtmp_stream">RTMP Stream</label>
          <input type="text" id="cfg_rtmp_stream">
        </div>
      </div>
    </div>

    <div class="panel">
      <h2><span class="h2-title"><span class="h2-icon">💡</span>Lampen &amp; Bereiche</span></h2>
      <div class="hint" style="margin-bottom: 12px;">Wird nur verwendet, wenn kein aktives Profil gesetzt ist (siehe „Allgemein“ oben).</div>
      <div class="field">
        <label>Lampen</label>
        <div class="picker" id="picker_light_entities"></div>
      </div>
      <div class="field" style="margin-top: 14px;">
        <label>Bereiche</label>
        <div class="picker" id="picker_area_ids"></div>
      </div>
    </div>

    <div class="panel">
      <h2><span class="h2-title"><span class="h2-icon">🏠</span>Raum-Profile</span></h2>
      <div id="profilesContainer"></div>
      <div class="empty" id="profilesEmpty">Noch keine Profile angelegt.</div>
      <button class="btn secondary small" style="margin-top: 8px;" onclick="addProfileCard()">+ Profil hinzufügen</button>
    </div>

    <div class="save-bar">
      <button class="btn" onclick="saveSettings()">▶ Speichern &amp; neu starten</button>
      <button class="btn secondary" onclick="loadSettings()">Verwerfen</button>
      <span class="save-status" id="saveStatus"></span>
    </div>

  </div>

</div>

<script>
/* ============================== STATUS TAB ============================== */
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

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "settings" && !settingsLoaded) {
    loadSettings();
  }
}

poll();

/* ============================== SETTINGS TAB ============================= */
let allLights = [];
let allAreas = [];
let settingsLoaded = false;
let profileCounter = 0;

const SLIDER_FORMATS = {
  cfg_sensitivity: v => Number(v).toFixed(2) + "x",
  cfg_beat_threshold: v => Number(v).toFixed(2),
  cfg_base_hue: v => Math.round(v) + "°",
  cfg_min_brightness: v => Math.round(v),
  cfg_max_brightness: v => Math.round(v),
  cfg_transition: v => Number(v).toFixed(2) + " s",
  cfg_update_interval_ms: v => Math.round(v) + " ms",
  cfg_silence_timeout_s: v => Math.round(v) + " s",
};

function updateSliderDisplay(id) {
  const el = document.getElementById(id);
  const out = document.getElementById("val_" + id);
  if (!el || !out) return;
  const fmt = SLIDER_FORMATS[id] || (v => v);
  out.textContent = fmt(el.value);
}

function setSliderValue(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = value;
  updateSliderDisplay(id);
}

Object.keys(SLIDER_FORMATS).forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => updateSliderDisplay(id));
});

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

/* Generic checkbox picker with a search box. Selection state lives in the
   checkboxes themselves, so filtering never loses what's checked. */
function buildPicker(container, items, idKey, labelFn, subLabelFn, selected) {
  container.innerHTML = "";
  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = `Suchen... (${items.length} verfügbar)`;
  container.appendChild(search);

  const count = document.createElement("div");
  count.className = "picker-count";
  container.appendChild(count);

  const list = document.createElement("div");
  list.className = "picker-list";
  container.appendChild(list);

  const selectedSet = new Set(selected || []);
  const rows = [];

  items.forEach(item => {
    const id = item[idKey];
    const row = document.createElement("label");
    row.className = "picker-row";
    row.dataset.searchtext = (labelFn(item) + " " + id).toLowerCase();

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = id;
    cb.checked = selectedSet.has(id);

    const labelText = document.createElement("span");
    labelText.textContent = labelFn(item);

    row.appendChild(cb);
    row.appendChild(labelText);

    const sub = subLabelFn ? subLabelFn(item) : null;
    if (sub) {
      const subEl = document.createElement("span");
      subEl.className = "entity-id";
      subEl.textContent = sub;
      row.appendChild(subEl);
    }

    list.appendChild(row);
    rows.push(row);
  });

  function updateCount() {
    const checked = rows.filter(r => r.querySelector("input").checked).length;
    count.textContent = `${checked} ausgewählt`;
  }
  rows.forEach(r => r.querySelector("input").addEventListener("change", updateCount));
  updateCount();

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    rows.forEach(r => {
      r.style.display = !q || r.dataset.searchtext.includes(q) ? "flex" : "none";
    });
  });

  container._getSelected = () => rows
    .filter(r => r.querySelector("input").checked)
    .map(r => r.querySelector("input").value);
}

function buildLightPicker(container, selected) {
  buildPicker(container, allLights, "entity_id", l => l.name,
    l => l.entity_id, selected);
}

function buildAreaPicker(container, selected) {
  buildPicker(container, allAreas, "area_id", a => a.name || a.area_id,
    null, selected);
}

function fillSelect(sel, options, current) {
  sel.innerHTML = "";
  options.forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    sel.appendChild(opt);
  });
  sel.value = current;
}

function profileCardHtml(id) {
  return `
    <div class="profile-card" id="profile-${id}" data-id="${id}">
      <div class="profile-card-head">
        <input type="text" placeholder="Profilname" class="p-name" style="max-width: 260px;">
        <button class="btn danger small" onclick="removeProfileCard(${id})">Entfernen</button>
      </div>
      <div class="field-row">
        <div class="field">
          <label>Raum</label>
          <input type="text" class="p-room" placeholder="z. B. Wohnzimmer">
        </div>
        <div class="field">
          <label>Modus (leer = global)</label>
          <select class="p-mode">
            <option value="">- global -</option>
            <option value="pulse">Pulse</option>
            <option value="spectrum">Spectrum</option>
            <option value="color_cycle">Color Cycle</option>
            <option value="brightness">Brightness</option>
            <option value="cinema">Cinema</option>
          </select>
        </div>
        <div class="field">
          <label>Empfindlichkeit</label>
          <input type="number" class="p-sensitivity" min="0.1" max="2.0" step="0.05" placeholder="global">
        </div>
        <div class="field">
          <label>Beat-Schwelle</label>
          <input type="number" class="p-beat_threshold" min="0.1" max="1.0" step="0.05" placeholder="global">
        </div>
        <div class="field">
          <label>Übergang (s)</label>
          <input type="number" class="p-transition" min="0" max="2.0" step="0.05" placeholder="global">
        </div>
        <div class="field">
          <label>Basis-Farbton</label>
          <input type="number" class="p-base_hue" min="0" max="360" step="1" placeholder="global">
        </div>
        <div class="field">
          <label>Min. Helligkeit</label>
          <input type="number" class="p-min_brightness" min="1" max="255" step="1" placeholder="global">
        </div>
        <div class="field">
          <label>Max. Helligkeit</label>
          <input type="number" class="p-max_brightness" min="1" max="255" step="1" placeholder="global">
        </div>
      </div>
      <div class="field" style="margin-top: 8px;">
        <label>Bereiche</label>
        <div class="picker" id="p-areas-${id}"></div>
      </div>
      <div class="band-grid">
        <div>
          <div class="band-label bass">Bass-Lampen</div>
          <div class="picker" id="p-bass-${id}"></div>
        </div>
        <div>
          <div class="band-label mid">Mitten-Lampen</div>
          <div class="picker" id="p-mid-${id}"></div>
        </div>
        <div>
          <div class="band-label high">Höhen-Lampen</div>
          <div class="picker" id="p-high-${id}"></div>
        </div>
        <div>
          <div class="band-label full">Alle Bänder / Fallback-Lampen</div>
          <div class="picker" id="p-full-${id}"></div>
        </div>
      </div>
    </div>`;
}

function addProfileCard(profile) {
  profile = profile || {};
  const id = profileCounter++;
  const container = document.getElementById("profilesContainer");
  const wrap = document.createElement("div");
  wrap.innerHTML = profileCardHtml(id);
  const card = wrap.firstElementChild;
  container.appendChild(card);

  card.querySelector(".p-name").value = profile.name || "";
  card.querySelector(".p-room").value = profile.room || "";
  card.querySelector(".p-mode").value = profile.mode || "";
  card.querySelector(".p-sensitivity").value = profile.sensitivity ?? "";
  card.querySelector(".p-beat_threshold").value = profile.beat_threshold ?? "";
  card.querySelector(".p-transition").value = profile.transition ?? "";
  card.querySelector(".p-base_hue").value = profile.base_hue ?? "";
  card.querySelector(".p-min_brightness").value = profile.min_brightness ?? "";
  card.querySelector(".p-max_brightness").value = profile.max_brightness ?? "";

  buildAreaPicker(document.getElementById(`p-areas-${id}`), profile.area_ids || []);
  buildLightPicker(document.getElementById(`p-bass-${id}`), profile.bass_lights || []);
  buildLightPicker(document.getElementById(`p-mid-${id}`), profile.mid_lights || []);
  buildLightPicker(document.getElementById(`p-high-${id}`), profile.high_lights || []);
  buildLightPicker(document.getElementById(`p-full-${id}`), profile.full_lights || []);

  updateProfilesEmptyState();
  updateActiveProfileOptions();
}

function removeProfileCard(id) {
  const el = document.getElementById(`profile-${id}`);
  if (el) el.remove();
  updateProfilesEmptyState();
  updateActiveProfileOptions();
}

function updateProfilesEmptyState() {
  const has = document.getElementById("profilesContainer").children.length > 0;
  document.getElementById("profilesEmpty").style.display = has ? "none" : "block";
}

function updateActiveProfileOptions() {
  const sel = document.getElementById("cfg_active_profile");
  const current = sel.value;
  const names = Array.from(document.querySelectorAll("#profilesContainer .p-name"))
    .map(i => i.value.trim())
    .filter(Boolean);
  fillSelect(sel, [["", "- kein Profil (Legacy) -"], ...names.map(n => [n, n])], current);
}

async function loadSettings() {
  const status = document.getElementById("saveStatus");
  status.textContent = "Lade Einstellungen...";
  status.className = "save-status";
  try {
    const [lightsRes, areasRes, cfgRes] = await Promise.all([
      fetch("api/lights", { cache: "no-store" }),
      fetch("api/areas", { cache: "no-store" }),
      fetch("api/config", { cache: "no-store" }),
    ]);
    allLights = await lightsRes.json();
    allAreas = await areasRes.json();
    const cfg = await cfgRes.json();

    document.getElementById("cfg_enabled").checked = cfg.enabled !== false;
    document.getElementById("cfg_mode").value = cfg.mode || "spectrum";
    document.getElementById("cfg_color_mode").value = cfg.color_mode || "spectrum";
    document.getElementById("cfg_log_level").value = cfg.log_level || "info";
    setSliderValue("cfg_sensitivity", cfg.sensitivity ?? 0.7);
    setSliderValue("cfg_beat_threshold", cfg.beat_threshold ?? 0.55);
    setSliderValue("cfg_update_interval_ms", cfg.update_interval_ms ?? 80);
    setSliderValue("cfg_transition", cfg.transition ?? 0.15);
    setSliderValue("cfg_silence_timeout_s", cfg.silence_timeout_s ?? 8);
    setSliderValue("cfg_base_hue", cfg.base_hue ?? 0);
    setSliderValue("cfg_min_brightness", cfg.min_brightness ?? 10);
    setSliderValue("cfg_max_brightness", cfg.max_brightness ?? 255);
    document.getElementById("cfg_rtmp_app").value = cfg.rtmp_app || "live";
    document.getElementById("cfg_rtmp_stream").value = cfg.rtmp_stream || "music";

    buildLightPicker(document.getElementById("picker_light_entities"), cfg.light_entities || []);
    buildAreaPicker(document.getElementById("picker_area_ids"), cfg.area_ids || []);

    document.getElementById("profilesContainer").innerHTML = "";
    profileCounter = 0;
    (cfg.profiles || []).forEach(p => addProfileCard(p));
    updateProfilesEmptyState();

    updateActiveProfileOptions();
    document.getElementById("cfg_active_profile").value = cfg.active_profile || "";

    settingsLoaded = true;
    status.textContent = "";
  } catch (e) {
    status.textContent = "Fehler beim Laden: " + e;
    status.className = "save-status err";
  }
}

function collectConfig() {
  const profiles = Array.from(document.querySelectorAll("#profilesContainer .profile-card")).map(card => {
    const id = card.dataset.id;
    const numOrUndef = (val) => val === "" ? undefined : Number(val);
    const p = {
      name: card.querySelector(".p-name").value.trim(),
      room: card.querySelector(".p-room").value.trim(),
      mode: card.querySelector(".p-mode").value || undefined,
      sensitivity: numOrUndef(card.querySelector(".p-sensitivity").value),
      beat_threshold: numOrUndef(card.querySelector(".p-beat_threshold").value),
      transition: numOrUndef(card.querySelector(".p-transition").value),
      base_hue: numOrUndef(card.querySelector(".p-base_hue").value),
      min_brightness: numOrUndef(card.querySelector(".p-min_brightness").value),
      max_brightness: numOrUndef(card.querySelector(".p-max_brightness").value),
      area_ids: document.getElementById(`p-areas-${id}`)._getSelected(),
      bass_lights: document.getElementById(`p-bass-${id}`)._getSelected(),
      mid_lights: document.getElementById(`p-mid-${id}`)._getSelected(),
      high_lights: document.getElementById(`p-high-${id}`)._getSelected(),
      full_lights: document.getElementById(`p-full-${id}`)._getSelected(),
    };
    Object.keys(p).forEach(k => { if (p[k] === undefined) delete p[k]; });
    return p;
  }).filter(p => p.name);

  return {
    enabled: document.getElementById("cfg_enabled").checked,
    active_profile: document.getElementById("cfg_active_profile").value,
    mode: document.getElementById("cfg_mode").value,
    sensitivity: Number(document.getElementById("cfg_sensitivity").value),
    update_interval_ms: Number(document.getElementById("cfg_update_interval_ms").value),
    transition: Number(document.getElementById("cfg_transition").value),
    min_brightness: Number(document.getElementById("cfg_min_brightness").value),
    max_brightness: Number(document.getElementById("cfg_max_brightness").value),
    color_mode: document.getElementById("cfg_color_mode").value,
    base_hue: Number(document.getElementById("cfg_base_hue").value),
    beat_threshold: Number(document.getElementById("cfg_beat_threshold").value),
    silence_timeout_s: Number(document.getElementById("cfg_silence_timeout_s").value),
    rtmp_app: document.getElementById("cfg_rtmp_app").value.trim(),
    rtmp_stream: document.getElementById("cfg_rtmp_stream").value.trim(),
    log_level: document.getElementById("cfg_log_level").value,
    light_entities: document.getElementById("picker_light_entities")._getSelected(),
    area_ids: document.getElementById("picker_area_ids")._getSelected(),
    profiles,
  };
}

async function saveSettings() {
  const status = document.getElementById("saveStatus");
  status.textContent = "Speichere...";
  status.className = "save-status";
  try {
    const payload = collectConfig();
    const res = await fetch("api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || ("HTTP " + res.status));
    status.textContent = "Gespeichert! Add-on wird neu gestartet...";
    status.className = "save-status ok";
  } catch (e) {
    status.textContent = "Fehler: " + e;
    status.className = "save-status err";
  }
}
</script>
</body>
</html>
"""


class WebRequestHandler(BaseHTTPRequestHandler):
    # Set by start_server() before the server starts accepting connections.
    state: WatchdogState = None  # type: ignore[assignment]

    server_version = "LarixMusicWebUI/2.0"

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

    def _send_json(self, status: int, payload) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE_HTML.encode("utf-8"))
            return

        if path == "/api/status":
            self._send_json(200, self.state.snapshot())
            return

        if path == "/api/config":
            self._send_json(200, configapi.read_current_options())
            return

        if path == "/api/lights":
            try:
                self._send_json(200, configapi.list_light_entities())
            except Exception as e:
                log.exception("Failed to list lights")
                self._send_json(500, {"error": str(e)})
            return

        if path == "/api/areas":
            try:
                self._send_json(200, configapi.list_areas())
            except Exception as e:
                log.exception("Failed to list areas")
                self._send_json(500, {"error": str(e)})
            return

        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw_body.decode("utf-8"))
                clean = configapi.sanitize_options(payload)
                configapi.save_options(clean)
                self.state.log_event("Einstellungen gespeichert - Add-on wird neu gestartet", "info")
                self._send_json(200, {"ok": True})
                # Restart after responding, so the browser gets confirmation first.
                threading.Thread(target=configapi.restart_addon, daemon=True).start()
            except configapi.ConfigApiError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                log.exception("Failed to save settings")
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        self._send(404, "text/plain; charset=utf-8", b"Not found")


def start_server(state: WatchdogState, host: str = "0.0.0.0", port: int = 8099) -> ThreadingHTTPServer:
    """Start the web UI (status dashboard + settings) in a background thread."""
    WebRequestHandler.state = state
    server = ThreadingHTTPServer((host, port), WebRequestHandler)
    thread = threading.Thread(target=server.serve_forever, name="webui", daemon=True)
    thread.start()
    log.info("Web UI listening on %s:%s", host, port)
    return server
    