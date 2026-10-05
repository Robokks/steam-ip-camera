# IP Camera Recorder

A small Python app that reads a live IP camera feed, shows it in a window, and records it to video.

## Features
- RTSP, HTTP/MJPEG, video files or USB webcams (`0`)
- Start/stop recording with a key, or record automatically from start-up
- Split recordings into fixed-length files (e.g. every 10 minutes)
- Date/time stamp burned into the video
- Automatic reconnect if the camera drops
- JPEG snapshots
- Headless mode for servers / Raspberry Pi

## Install
```bash
pip install -r requirements.txt
```

## Usage
```bash
python ip_camera_recorder.py <camera-url> [options]
```

| Key | Action |
|-----|--------|
| `r` | Start / stop recording |
| `s` | Save snapshot |
| `q` / `Esc` | Quit |

### Examples
```bash
# View the camera; press r to record
python ip_camera_recorder.py "rtsp://admin:password@192.168.1.10:554/stream1"

# Phone "IP Webcam" app (MJPEG), record right away
python ip_camera_recorder.py http://192.168.1.20:8080/video --record

# 24/7 headless recording in 10-minute files
python ip_camera_recorder.py "rtsp://..." --record --no-display --segment 600 -o /data/cctv

# Record for 1 hour then exit
python ip_camera_recorder.py "rtsp://..." --record --duration 3600
```

### Options
| Option | Description |
|--------|-------------|
| `-o, --output DIR` | Output folder (default `recordings`) |
| `--record` | Start recording immediately |
| `--duration SEC` | Stop after N seconds |
| `--segment SEC` | Start a new file every N seconds |
| `--fps N` | Recording FPS (default: camera FPS, or 20) |
| `--format mp4\|avi\|mkv` | Video container (default `mp4`) |
| `--width PX` | Resize frames to this width |
| `--prefix NAME` | File name prefix (default `cam`) |
| `--no-timestamp` | Do not burn the timestamp into the video |
| `--no-display` | Run without a window |

### Common camera URLs
| Brand | URL |
|-------|-----|
| Hikvision | `rtsp://user:pass@IP:554/Streaming/Channels/101` |
| Dahua / Amcrest | `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0` |
| TP-Link Tapo | `rtsp://user:pass@IP:554/stream1` |
| Reolink | `rtsp://user:pass@IP:554/h264Preview_01_main` |
| Android IP Webcam | `http://IP:8080/video` |

Tip: quote the URL in the shell if it contains `&` or `?`. If the password contains special characters such as `@`, URL-encode them (`@` → `%40`).
