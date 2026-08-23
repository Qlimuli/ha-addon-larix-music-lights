# Home Assistant Add-on: Larix Music Reactive Lights

Stream your microphone (or any audio) from **Larix Broadcaster** into Home Assistant and make your lights react to the music in real time.

## Features (v1.1)

- Receives **RTMP** streams from Larix Broadcaster
- Real-time audio analysis (FFT, amplitude, bass / mids / highs, beat detection)
- **Band-specific light assignment** – e.g. certain lamps only for bass
- **Room profiles** – save multiple configurations and switch via `active_profile`
- Modes: Pulse, Spectrum, Color Cycle, Brightness, Cinema
- Completely local – no cloud

## Installation

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Add: `https://github.com/Qlimuli/ha-addon-larix-music-lights`
3. Install **Larix Music Reactive Lights**
4. Configure profiles / lights (see Documentation)
5. Start the add-on

## Quick Larix setup

RTMP URL: `rtmp://<HA-IP>:1935/live/music`

## Documentation

Full docs (German + examples): [larix_music_lights/DOCS.md](larix_music_lights/DOCS.md)

## License

MIT
