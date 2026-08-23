#!/usr/bin/env python3
"""
Larix Music Reactive Lights - Watchdog State
Thread-safe container for live status information, shared between the
audio-analysis loop (analyzer.py) and the embedded web UI (webui.py).
"""

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

# Only keep the last N events in memory (shown in the GUI event log)
MAX_EVENTS = 50


class WatchdogState:
    """Holds everything the Watchdog GUI needs to render live status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

        self.started_at: float = time.time()
        self.enabled: bool = True

        # "starting" | "disabled" | "waiting" | "connected" | "no_signal" | "stopped"
        self.connection_state: str = "starting"

        self.last_chunk_at: Optional[float] = None
        self.last_feature_update_at: Optional[float] = None
        self.last_light_update_at: Optional[float] = None

        self.ffmpeg_restarts: int = 0
        self.stream_connections: int = 0

        self.last_error: Optional[str] = None
        self.last_error_at: Optional[float] = None

        self.profile_name: str = ""
        self.room: str = ""
        self.mode: str = ""
        self.sensitivity: float = 0.0
        self.rtmp_url: str = ""

        self.bands: Dict[str, List[str]] = {"bass": [], "mid": [], "high": [], "full": []}
        self.features: Dict[str, float] = {
            "amplitude": 0.0,
            "bass": 0.0,
            "mid": 0.0,
            "high": 0.0,
            "beat": 0.0,
        }

        self.beat_count: int = 0
        self.last_beat_at: Optional[float] = None

        self._events: deque = deque(maxlen=MAX_EVENTS)

    # ------------------------------------------------------------------
    # Mutators (called from analyzer.py)
    # ------------------------------------------------------------------
    def set(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def set_features(self, features: Dict[str, float]) -> None:
        with self._lock:
            self.features = dict(features)
            self.last_feature_update_at = time.time()
            if features.get("beat", 0.0) > 0.5:
                self.beat_count += 1
                self.last_beat_at = time.time()

    def mark_light_update(self) -> None:
        with self._lock:
            self.last_light_update_at = time.time()

    def mark_chunk_received(self) -> None:
        with self._lock:
            self.last_chunk_at = time.time()
            if self.connection_state != "connected":
                self.connection_state = "connected"
                self.stream_connections += 1
                self._log_locked(
                    "Larix Broadcaster verbunden - Audio-Stream aktiv", "success"
                )

    def mark_waiting(self) -> None:
        with self._lock:
            if self.connection_state != "waiting":
                self.connection_state = "waiting"

    def mark_no_signal(self) -> None:
        with self._lock:
            if self.connection_state == "connected":
                self.connection_state = "no_signal"
                self._log_locked("Kein Audio-Signal mehr empfangen", "warning")

    def mark_ffmpeg_restart(self) -> None:
        with self._lock:
            self.ffmpeg_restarts += 1
            self.connection_state = "waiting"
            self._log_locked(
                f"FFmpeg neu gestartet (#{self.ffmpeg_restarts})", "warning"
            )

    def mark_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message
            self.last_error_at = time.time()
            self._log_locked(f"Fehler: {message}", "error")

    def mark_stopped(self) -> None:
        with self._lock:
            self.connection_state = "stopped"
            self._log_locked("Add-on wird beendet", "info")

    def log_event(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._log_locked(message, level)

    def _log_locked(self, message: str, level: str) -> None:
        self._events.appendleft({"t": time.time(), "level": level, "message": message})

    # ------------------------------------------------------------------
    # Reader (called from webui.py)
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            return {
                "now": now,
                "started_at": self.started_at,
                "uptime_s": now - self.started_at,
                "enabled": self.enabled,
                "connection_state": self.connection_state,
                "last_chunk_age_s": (now - self.last_chunk_at) if self.last_chunk_at else None,
                "last_light_update_age_s": (
                    (now - self.last_light_update_at) if self.last_light_update_at else None
                ),
                "ffmpeg_restarts": self.ffmpeg_restarts,
                "stream_connections": self.stream_connections,
                "last_error": self.last_error,
                "last_error_age_s": (now - self.last_error_at) if self.last_error_at else None,
                "profile_name": self.profile_name,
                "room": self.room,
                "mode": self.mode,
                "sensitivity": self.sensitivity,
                "rtmp_url": self.rtmp_url,
                "bands": self.bands,
                "features": self.features,
                "beat_count": self.beat_count,
                "last_beat_age_s": (now - self.last_beat_at) if self.last_beat_at else None,
                "events": list(self._events),
            }
