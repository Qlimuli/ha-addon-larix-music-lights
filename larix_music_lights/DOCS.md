# Larix Music Reactive Lights

Dieses Add-on empfängt einen Audio-Stream von **Larix Broadcaster** (iOS/Android) und steuert Home-Assistant-Lampen in Echtzeit zur Musik.

**Neu in 1.1:**
- **Band-Zuweisung**: Bass-, Mid- und High-Lampen getrennt konfigurieren
- **Profile pro Raum**: Mehrere benannte Profile speichern und per `active_profile` auswählen

## Voraussetzungen

- Home Assistant OS / Supervised
- Larix Broadcaster App
- Mindestens eine steuerbare `light.*`-Entität

## Installation

1. Add-on-Store → Repositories → `https://github.com/Qlimuli/ha-addon-larix-music-lights` hinzufügen
2. **Larix Music Reactive Lights** installieren
3. Konfiguration anpassen und starten

## Larix Broadcaster einrichten

1. App öffnen → **Connections** → **+**
2. Neuen **RTMP**-Eintrag anlegen:
   - **URL**: `rtmp://<IP-deines-HA>:1935/live/music`
3. Optional: **Audio only** aktivieren
4. Stream starten

## Profile & Band-Zuweisung (empfohlen)

Statt einer flachen Liste legst du **Profile** an. Jedes Profil kann einem Raum zugeordnet werden und hat eigene Bass-/Mid-/High-/Full-Lampen.

### Beispiel-Konfiguration

```yaml
enabled: true
active_profile: Wohnzimmer Party   # Name des aktiven Profils

# Globale Defaults (werden vom Profil überschrieben, falls gesetzt)
mode: spectrum
sensitivity: 0.7
update_interval_ms: 80
transition: 0.15
min_brightness: 10
max_brightness: 255
beat_threshold: 0.55
silence_timeout_s: 8
rtmp_app: live
rtmp_stream: music
log_level: info

profiles:
  - name: Wohnzimmer Party
    room: Wohnzimmer
    area_ids:
      - wohnzimmer
    mode: spectrum
    sensitivity: 0.85
    bass_lights:
      - light.wohnzimmer_bass_links
      - light.wohnzimmer_bass_rechts
    mid_lights:
      - light.wohnzimmer_stehlampe
    high_lights:
      - light.wohnzimmer_deckenspots
    full_lights:
      - light.wohnzimmer_decke

  - name: Schlafzimmer Chill
    room: Schlafzimmer
    area_ids:
      - schlafzimmer
    mode: cinema
    sensitivity: 0.5
    min_brightness: 5
    max_brightness: 120
    full_lights:
      - light.schlafzimmer_nachttisch
      - light.schlafzimmer_decke

  - name: Küche
    room: Küche
    mode: brightness
    full_lights:
      - light.kueche_decke
```

### Wie die Bänder wirken

| Band | Frequenzbereich | Typische Nutzung |
|------|-----------------|------------------|
| **bass_lights** | ~20–150 Hz | Subwoofer-Feeling, starke Beats, rote Farbe im Spectrum-Modus |
| **mid_lights** | ~150–2000 Hz | Vocals, Gitarren, grüne Farbe |
| **high_lights** | ~2000–8000 Hz | Hi-Hats, Cymbals, blaue Farbe |
| **full_lights** | Gesamt-Amplitude | Fallback / Gesamt-Helligkeit / Pulse-Flash |

- Wenn nur `full_lights` (oder `area_ids`) gesetzt sind, verhalten sich alle Lampen wie bisher (Gesamtreaktion).
- Wenn Band-Listen gesetzt sind, werden die Bänder **getrennt** angesteuert.
- `area_ids` holt automatisch alle `light.*` des Raums und nutzt sie als Fallback für `full_lights`, falls keine expliziten Listen da sind.

### Profil wechseln

Ändere einfach `active_profile` auf den gewünschten Namen und starte das Add-on neu (oder speichere die Konfiguration – der Supervisor startet neu).

## Legacy-Modus (ohne Profile)

Falls `profiles` leer ist, greifen weiterhin die alten Optionen:

```yaml
light_entities:
  - light.wohnzimmer_decke
area_ids:
  - wohnzimmer
mode: pulse
```

## Modi

- **pulse** – Beat-Flash vor allem auf `bass_lights` (sonst `full_lights`)
- **spectrum** – Jedes Band steuert seine eigenen Lampen mit passender Farbe
- **color_cycle** – Langsamer Farbwechsel, Bass beschleunigt
- **brightness** – Nur Helligkeit, optional pro Band
- **cinema** – Warme, gedimmte Atmosphäre

## Tipps

- Starte mit einem Profil und nur `full_lights`, dann ergänze Bass/Mid/High.
- Bei zu starker Reaktion `sensitivity` senken oder `beat_threshold` erhöhen.
- „Audio only“ in Larix spart Bandbreite und CPU.

## Fehlerbehebung

- **Keine Reaktion** → Log prüfen („Waiting for Larix…“ / Profilname / Band-Listen).  
- **Falsches Profil** → `active_profile` muss **exakt** dem `name` eines Profils entsprechen.  
- **Port belegt** → Host-Port-Mapping in der Add-on-Konfiguration ändern.

## Lizenz

MIT
