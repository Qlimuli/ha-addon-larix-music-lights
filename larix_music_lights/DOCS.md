# Larix Music Reactive Lights

Dieses Add-on empfängt einen Audio-Stream von **Larix Broadcaster** (iOS/Android) und steuert Home-Assistant-Lampen in Echtzeit zur Musik.

## Voraussetzungen

- Home Assistant OS / Supervised
- Larix Broadcaster App (kostenlos im App Store / Play Store)
- Mindestens eine steuerbare `light.*`-Entität

## Installation

1. Add-on-Store → Repositories → `https://github.com/Qlimuli/ha-addon-larix-music-lights` hinzufügen
2. **Larix Music Reactive Lights** installieren
3. Konfiguration anpassen und starten

## Larix Broadcaster einrichten

1. App öffnen → **Connections** → **+**
2. Neuen **RTMP**-Eintrag anlegen:
   - **URL**: `rtmp://<IP-deines-HA>:1935/live/music`
   - Name z. B. „HA Music Lights“
3. Optional: In den Audio-Einstellungen **Audio only** aktivieren (spart Bandbreite und CPU)
4. Stream starten

Der Standard-Port ist **1935** (RTMP). Dieser kann in der Add-on-Konfiguration geändert werden (Port-Mapping).

### Optional: SRT

Das Add-on exponiert auch UDP 8890. SRT-Unterstützung kann später erweitert werden; aktuell ist RTMP der primäre und stabilste Weg.

## Konfiguration

| Option | Typ | Standard | Beschreibung |
|--------|-----|----------|--------------|
| `enabled` | bool | `true` | Add-on aktiv |
| `light_entities` | list | `[]` | Liste von `light.xxx` Entitäten |
| `area_ids` | list | `[]` | Area-IDs (ganze Räume) |
| `mode` | list | `pulse` | `pulse`, `spectrum`, `color_cycle`, `brightness`, `cinema` |
| `sensitivity` | float | `0.7` | Empfindlichkeit (0.1–2.0) |
| `update_interval_ms` | int | `80` | Update-Rate der Lampen (ms) |
| `transition` | float | `0.15` | Übergangszeit (s) |
| `min_brightness` | int | `10` | Minimale Helligkeit |
| `max_brightness` | int | `255` | Maximale Helligkeit |
| `color_mode` | list | `spectrum` | Farblogik (wird von manchen Modes verwendet) |
| `base_hue` | int | `0` | Start-Farbton (0–360) |
| `beat_threshold` | float | `0.55` | Schwelle für Beat-Erkennung |
| `silence_timeout_s` | int | `8` | Nach X Sekunden Stille werden die Lampen nicht mehr angesteuert |
| `rtmp_app` | str | `live` | RTMP Application-Name |
| `rtmp_stream` | str | `music` | RTMP Stream-Name |
| `log_level` | list | `info` | Log-Level |

### Beispiel-Konfiguration

```yaml
enabled: true
light_entities:
  - light.wohnzimmer_decke
  - light.wohnzimmer_stehlampe
area_ids:
  - wohnzimmer
mode: spectrum
sensitivity: 0.8
update_interval_ms: 70
transition: 0.12
min_brightness: 15
max_brightness: 255
beat_threshold: 0.5
silence_timeout_s: 10
rtmp_app: live
rtmp_stream: music
log_level: info
```

## Modi erklärt

- **pulse** – Kurzer heller Flash bei Beats (Bass), ansonsten abgedunkelt. Gut für Partys.
- **spectrum** – Farbe und Helligkeit folgen den Frequenzbändern (Bass/Mids/Highs).
- **color_cycle** – Langsamer Farbwechsel, der sich mit dem Bass beschleunigt.
- **brightness** – Nur Helligkeit folgt der Gesamtlautstärke, Farbe bleibt unverändert.
- **cinema** – Warme, gedimmte Atmosphäre mit leichter Bass-Reaktion (Film-/Chill-Modus).

## Tipps

- Starte mit wenigen Lampen und `mode: pulse` oder `brightness`.
- Bei zu aggressiver Reaktion `sensitivity` senken oder `beat_threshold` erhöhen.
- Für bessere Performance „Audio only“ in Larix aktivieren.
- Die Analyse läuft komplett lokal – keine Cloud, keine externen Dienste.

## Fehlerbehebung

- **Keine Reaktion der Lampen**  
  - Prüfe, ob der Stream in Larix wirklich läuft (Status „Streaming“).  
  - Log des Add-ons öffnen – dort siehst du „Waiting for Larix…“ und später die Analyse-Werte.  
  - Stelle sicher, dass die angegebenen `light_entities` existieren und schaltbar sind.

- **FFmpeg startet neu**  
  - Normal, wenn der Stream in Larix beendet wird. Sobald wieder gestreamt wird, verbindet sich das Add-on erneut.

- **Port bereits belegt**  
  - Ändere das Host-Port-Mapping in der Add-on-Konfiguration (z. B. 1936).

## Lizenz

MIT
