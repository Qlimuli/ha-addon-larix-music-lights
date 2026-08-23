#!/usr/bin/with-contenv bashio
# ==============================================================================
# Larix Music Reactive Lights Add-on
# ==============================================================================

set -e

bashio::log.info "Starting Larix Music Reactive Lights..."

# Read configuration
ENABLED=$(bashio::config 'enabled')
MODE=$(bashio::config 'mode')
SENSITIVITY=$(bashio::config 'sensitivity')
UPDATE_MS=$(bashio::config 'update_interval_ms')
TRANSITION=$(bashio::config 'transition')
MIN_BRIGHT=$(bashio::config 'min_brightness')
MAX_BRIGHT=$(bashio::config 'max_brightness')
COLOR_MODE=$(bashio::config 'color_mode')
BASE_HUE=$(bashio::config 'base_hue')
BEAT_THRESH=$(bashio::config 'beat_threshold')
SILENCE_S=$(bashio::config 'silence_timeout_s')
RTMP_APP=$(bashio::config 'rtmp_app')
RTMP_STREAM=$(bashio::config 'rtmp_stream')
LOG_LEVEL=$(bashio::config 'log_level')

# Light entities as JSON array
LIGHT_ENTITIES=$(bashio::config 'light_entities' | jq -c '.' 2>/dev/null || echo '[]')
AREA_IDS=$(bashio::config 'area_ids' | jq -c '.' 2>/dev/null || echo '[]')

# Supervisor token for HA API
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-}"

# Export all config for the Python process
export ADDON_ENABLED="${ENABLED}"
export ADDON_MODE="${MODE}"
export ADDON_SENSITIVITY="${SENSITIVITY}"
export ADDON_UPDATE_MS="${UPDATE_MS}"
export ADDON_TRANSITION="${TRANSITION}"
export ADDON_MIN_BRIGHT="${MIN_BRIGHT}"
export ADDON_MAX_BRIGHT="${MAX_BRIGHT}"
export ADDON_COLOR_MODE="${COLOR_MODE}"
export ADDON_BASE_HUE="${BASE_HUE}"
export ADDON_BEAT_THRESH="${BEAT_THRESH}"
export ADDON_SILENCE_S="${SILENCE_S}"
export ADDON_RTMP_APP="${RTMP_APP}"
export ADDON_RTMP_STREAM="${RTMP_STREAM}"
export ADDON_LOG_LEVEL="${LOG_LEVEL}"
export ADDON_LIGHT_ENTITIES="${LIGHT_ENTITIES}"
export ADDON_AREA_IDS="${AREA_IDS}"

bashio::log.info "Mode: ${MODE} | Sensitivity: ${SENSITIVITY} | Entities: ${LIGHT_ENTITIES}"
bashio::log.info "Waiting for RTMP stream on rtmp://0.0.0.0:1935/${RTMP_APP}/${RTMP_STREAM}"

# Start the analyzer (it will launch ffmpeg itself)
exec python3 /app/analyzer.py
