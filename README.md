# Home Assistant Add-on: Larix Music Reactive Lights

Stream your microphone (or any audio) from **Larix Broadcaster** (iOS/Android) into Home Assistant and make your lights react to the music in real time.

## Features

- Receives **RTMP** (and optionally SRT) streams from Larix Broadcaster
- Real-time audio analysis (FFT, amplitude, bass/mids/highs, beat detection)
- Configurable light control:
  - Single lights, groups or whole areas/rooms
  - On/Off switch
  - Modes: Pulse (beat), Spectrum, Color Cycle, Brightness only, Cinema
- Sensitivity, update rate, color palette and transition settings
- Works completely local – no cloud required

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**
2. Click the three dots → **Repositories**
3. Add: `https://github.com/Qlimuli/ha-addon-larix-music-lights`
4. Install **Larix Music Reactive Lights**
5. Configure the add-on (see Documentation)
6. Start it

## Larix Broadcaster Setup

1. Open Larix Broadcaster → Connections → +
2. Create an **RTMP** connection:
   - URL: `rtmp://<HA-IP>:1935/live/music`
   - Name: e.g. “HA Music”
3. (Optional) Enable **Audio only** in the Audio menu of Larix for lower bandwidth
4. Start streaming

The default RTMP port is **1935**. You can change it in the add-on configuration.

## Documentation

See the [add-on documentation](larix_music_lights/DOCS.md) for full configuration options.

## License

MIT
