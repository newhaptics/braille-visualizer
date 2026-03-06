# Codex Visualizer

Real-time visualization of the Codex braille display, touch input, and gesture events.

## Prerequisites

- Python 3.10+
- NexusProxy running on port 26541 (Codex connected via USB)

## Setup

```bash
cd backend
pip install -r requirements.txt
python main.py
```

Open [http://localhost:8000](http://localhost:8000) in a browser.

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `NEXUSPROXY_HOST` | `127.0.0.1` | NexusProxy TCP host |
| `NEXUSPROXY_PORT` | `26541` | NexusProxy TCP port |
| `RELAY_PORT` | `8000` | Web server port |

## Architecture

```
Codex <-USB serial-> NexusProxy:26541 <-TCP (SIG frames)-> Relay (:8000) <-WebSocket (JSON)-> Browser
```

The Python relay connects to NexusProxy, decodes binary SIG frames into JSON, and broadcasts to browser clients over WebSocket. The frontend is a single self-contained HTML file with no external dependencies.

## Connection Indicators

- **Web Server** — Browser to relay server WebSocket
- **Codex** — Device data activity (green = receiving data, amber = no data for 5s, gray = NexusProxy not connected)

## Supported Signals

Display: `PrintDisplay` | Touch: `Touch` (10-point, ~60Hz) | Taps: `DoubleTap`, `OneFingerTripleTap` | Keys: `Keystroke` | Gestures: `TwoFingerDoubleTap`, `TwoFingerSwipe`, `ThreeFingerSwipe`, `FourFingerSwipe`, `FourFingerPinch`, `FourFingerSpread`, `EightFingerDoubleTap`, `EightFingerHold`

