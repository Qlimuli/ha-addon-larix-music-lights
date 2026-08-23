#!/usr/bin/with-contenv bashio
# ==============================================================================
# Larix Music Reactive Lights Add-on
# ==============================================================================

set -e

bashio::log.info "Starting Larix Music Reactive Lights..."

# Prefer the Supervisor options file – most reliable source for arrays
OPTIONS_FILE="/data/options.json"
if [ ! -f "${OPTIONS_FILE}" ]; then
  # Fallback path used by some Supervisor versions
  OPTIONS_FILE="/data/options.json"
fi

# Helper: read a key from options.json as compact JSON
opt_json() {
  local key="$1"
  local default="$2"
  if [ -f "${OPTIONS_FILE}" ]; then
    jq -c --arg k "$key" '.[$k] // empty' "${OPTIONS_FILE}" 2>/dev/null | {
      read -r val
      if [ -n "$val" ] && [ "$val" != "null" ]; then
        echo "$val"
      else
        echo "$default"
      fi
    }
  else
    echo "$default"
  fi
}

opt_str() {
  local key="$1"
  local default="$2"
  if [ -f "${OPTIONS_FILE}" ]; then
    local v
    v=$(jq -r --arg k "$key" '.[$k] // empty' "${OPTIONS_FILE}" 2>/dev/null || true)
    if [ -n "$v" ] && [ "$v" != "null" ]; then
      echo "$v"
    else
      echo "$default"
    fi
  else
    echo "$default"
  fi
}

ENABLED=$(opt_str enabled true)
ACTIVE_PROFILE=$(opt_str active_profile "")
MODE=$(opt_str mode spectrum)
SENSITIVITY=$(opt_str sensitivity 0.7)
UPDATE_MS=$(opt_str update_interval_ms 80)
TRANSITION=$(opt_str transition 0.15)
MIN_BRIGHT=$(opt_str min_brightness 10)
MAX_BRIGHT=$(opt_str max_brightness 255)
COLOR_MODE=$(opt_str color_mode spectrum)
BASE_HUE=$(opt_str base_hue 0)
BEAT_THRESH=$(opt_str beat_threshold 0.55)
SILENCE_S=$(opt_str silence_timeout_s 8)
RTMP_APP=$(opt_str rtmp_app live)
RTMP_STREAM=$(opt_str rtmp_stream music)
LOG_LEVEL=$(opt_str log_level info)

LIGHT_ENTITIES=$(opt_json light_entities '[]')
AREA_IDS=$(opt_json area_ids '[]')
PROFILES=$(opt_json profiles '[]')

# Ensure profiles is always a JSON array (not an object)
PROFILES=$(echo "${PROFILES}" | jq -c 'if type == "array" then . else [] end' 2>/dev/null || echo '[]')
LIGHT_ENTITIES=$(echo "${LIGHT_ENTITIES}" | jq -c 'if type == "array" then . else [] end' 2>/dev/null || echo '[]')
AREA_IDS=$(echo "${AREA_IDS}" | jq -c 'if type == "array" then . else [] end' 2>/dev/null || echo '[]')

export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-}"

export ADDON_ENABLED="${ENABLED}"
export ADDON_ACTIVE_PROFILE="${ACTIVE_PROFILE}"
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
export ADDON_PROFILES="${PROFILES}"
export ADDON_OPTIONS_FILE="${OPTIONS_FILE}"

bashio::log.info "Options file: ${OPTIONS_FILE}"
bashio::log.info "Active profile: '${ACTIVE_PROFILE}' | Mode: ${MODE}"
bashio::log.info "Light entities: ${LIGHT_ENTITIES}"
bashio::log.info "Profiles count: $(echo "${PROFILES}" | jq 'length')"
bashio::log.info "Waiting for RTMP stream on rtmp://0.0.0.0:1935/${RTMP_APP}/${RTMP_STREAM}"

exec python3 /app/analyzer.py
