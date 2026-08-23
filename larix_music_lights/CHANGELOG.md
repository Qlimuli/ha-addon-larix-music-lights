# Changelog

## 1.3.0
- **Neuer Tab "Einstellungen" im Ingress-Panel**: die komplette Konfiguration lässt sich jetzt per GUI bearbeiten statt nur über den YAML-Tab "Konfiguration" im Supervisor
- Lampen-Auswahl über eine durchsuchbare Checkbox-Liste (Name + Entity-ID) statt manuellem Eintippen von `entity_id`s
- Bereichs-Auswahl (Areas) ebenfalls als Checkbox-Liste
- Raum-Profile lassen sich direkt in der GUI anlegen, bearbeiten und löschen, inkl. separater Bass-/Mitten-/Höhen-/Fallback-Lampenauswahl pro Profil
- "Speichern & neu starten" schreibt die Optionen über die Supervisor-API und startet das Add-on automatisch neu, damit Änderungen sofort aktiv werden
- Erfordert neu `hassio_api: true` + `hassio_role: manager`, damit das Add-on seine eigenen Optionen schreiben und sich selbst neu starten darf

## 1.2.0
- **Watchdog-GUI**: Neues Ingress-Panel ("Larix Music Reactive Lights" in der HA-Seitenleiste) mit Live-Status
- Zeigt Verbindungsstatus (Startet / Wartet auf Larix / Verbunden / Kein Signal / Deaktiviert / Gestoppt)
- Live-Pegel-Meter für Bass, Mitten, Höhen und Amplitude inkl. Beat-Anzeige
- Aktives Profil, Modus, Laufzeit, Anzahl FFmpeg-Neustarts und Stream-Verbindungen
- Ereignis-Log der letzten Verbindungs-/Fehlerereignisse
- Läuft komplett lokal ohne externe Ressourcen (kein CDN, kein zusätzlicher Port erforderlich)

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
