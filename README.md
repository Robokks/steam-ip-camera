# IP Camera Recorder

## LabVIEW
See [LABVIEW.md](LABVIEW.md). LabVIEW calls `labview_camera.py` through the Python Node.

## TCP remote control (start/stop trigger)
The Windows app runs a TCP server (default port **5000**, can be switched on or off in the app).
Send one ASCII command per line. The app answers with one line ending in CR LF:

| Send | Reply |
|---|---|
| `PING` | `OK PONG` |
| `START` | `OK START;<Save folder>\\cam_YYYYMMDD_HHMMSS.mp4` (default folder + date/time name) |
| `START;C:\Recordings;Test_001` | `OK START;C:\Recordings\Test_001.mp4` |
| `START;;Test_002` | empty field = folder set in the app |
| `STOP` | `OK STOP;<saved file path>` (sent after the file is closed) |
| `STATUS` | `OK RECORDING;<path>` / `OK IDLE` / `OK WAITING_MOTION` / `OK CONNECTING` / `OK DISCONNECTED` |
| `SNAPSHOT;C:\Pics;part_17` | `OK SNAPSHOT;C:\Pics\part_17.jpg` |
| `CONNECT` / `DISCONNECT` | `OK CONNECT` / `OK DISCONNECT` |
| anything wrong | `ERROR <reason>` (e.g. `ERROR ALREADY RECORDING;<path>`, `ERROR NOT RECORDING`) |

- `START` connects to the camera automatically if needed.
- An existing file is never overwritten; a date/time is added to the name instead.
- Long recordings are split as `<name>.mp4`, `<name>_part2.mp4`, …
- Characters not allowed in Windows file names are replaced with `_`.
- The manual buttons in the app keep working alongside TCP.

Test from a command prompt: `python tcp_client.py START C:\Recordings Test_001`, then `python tcp_client.py STOP`.
In LabVIEW: TCP Open Connection → TCP Write `START;C:\Recordings;Test_001\r\n` → TCP Read (mode **CRLF**) → TCP Close Connection.
See [LABVIEW.md](LABVIEW.md#tcp-remote-control-from-labview).

## File trigger
Tick **Enable file trigger** in the app and choose the command file (default `C:\CameraTrigger\command.txt`).
Write the command into that file from LabVIEW, a script or Notepad. Within about half a second the app:
1. runs it,
2. writes the result to `command_reply.txt` in the same folder,
3. **deletes** `command.txt`. A missing file means the command has been taken.

The file can use either format:
```
START                              CMD=START
START;C:\Recordings;Test_001       FOLDER=C:\Recordings
STOP                               FILE=Test_001
```
- `START` with no folder or name saves to the app's **Save folder** with a date/time name.
- `CMD=STOP` (or just `STOP`) stops recording.
- Lines starting with `#` are ignored.

## Windows app (`camera_app.py`)
A desktop app with live preview and one-click recording.

- Live preview, auto-reconnect
- Start / stop recording, snapshots
- Record only when motion is detected (3 s pre-roll)
- New file every N minutes, date/time on video
- **Connect when app starts** (on by default): opens straight to live view and listens for TCP or file triggers, **without recording**
- **Also start recording when app starts** (off by default)
- Settings are remembered (`%APPDATA%\IPCameraRecorder\settings.json`)

**Run from source:** `pip install -r requirements.txt` then `python camera_app.py`

**Make an .exe:** double-click `build_exe.bat` → `dist\IPCameraRecorder.exe`.
Every push also builds the exe on GitHub: open the repo's **Actions** tab →
*Build Windows app* → latest run → download **IPCameraRecorder-windows**.

---

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

## Matrix camera recorder (`matrix_record.py`)
A simpler script with three modes: `continuous` (24x7, split files), `timed` and `motion`.
```bash
CAM_IP=192.168.1.100 CAM_USER=admin CAM_PASS=secret python matrix_record.py continuous 10
python matrix_record.py timed 300
python matrix_record.py motion            # 3 s pre-roll, stops 10 s after motion ends
```
Set `CAM_URL` to use a full custom RTSP URL.
