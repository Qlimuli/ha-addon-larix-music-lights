#!/usr/bin/env python3
"""Larix Music Reactive Lights - Web UI (v1.6)"""

import json
import logging
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import configapi
from watchdog_state import WatchdogState

log = logging.getLogger("larix-music.webui")

_HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"
RELOAD_CALLBACK = None


def _page_html() -> bytes:
    return _HTML_PATH.read_bytes()


class WebRequestHandler(BaseHTTPRequestHandler):
    state: WatchdogState = None  # type: ignore[assignment]
    server_version = "LarixMusicWebUI/1.6"

    def log_message(self, fmt: str, *args) -> None:
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

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", _page_html())
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

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        if path == "/api/config":
            try:
                clean = configapi.sanitize_options(payload)
                configapi.save_options(clean)
                configapi.touch_reload_flag()
                if RELOAD_CALLBACK:
                    try:
                        RELOAD_CALLBACK()
                    except Exception as e:
                        log.warning("reload callback: %s", e)
                self._send_json(200, {"ok": True, "live": True, "options": clean})
            except configapi.ConfigApiError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                log.exception("Failed to save settings")
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/restart":
            self._send_json(200, {"ok": True, "restarting": True})
            threading.Thread(target=configapi.restart_addon, daemon=True).start()
            return

        self._send_json(404, {"ok": False, "error": "not found"})


def start_server(state: WatchdogState, port: int = 8099) -> ThreadingHTTPServer:
    WebRequestHandler.state = state
    srv = ThreadingHTTPServer(("0.0.0.0", port), WebRequestHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log.info("Web UI listening on 0.0.0.0:%s", port)
    return srv
