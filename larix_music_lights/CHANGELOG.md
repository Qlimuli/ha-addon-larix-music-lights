# Changelog

## 1.1.0
- **Profile pro Raum**: Mehrere benannte Profile speichern (`profiles` + `active_profile`)
- **Band-Zuweisung**: `bass_lights`, `mid_lights`, `high_lights`, `full_lights` pro Profil
- Spectrum-Modus steuert Bänder getrennt (Bass=rot, Mid=grün, High=blau)
- Profile können Mode, Sensitivity, Brightness etc. überschreiben
- Legacy-Modus (`light_entities` / `area_ids`) bleibt kompatibel

## 1.0.0
- Initial release
- RTMP listener for Larix Broadcaster
- Real-time FFT analysis (bass / mid / high / amplitude / beat)
- Modes: pulse, spectrum, color_cycle, brightness, cinema
- Configurable lights & areas, sensitivity, transitions
- Full Home Assistant Supervisor API integration
