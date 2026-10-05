# Using the IP camera recorder from LabVIEW

There are three ways to use it. The easiest is **TCP remote control**: run the
Windows app (`camera_app.py` / `IPCameraRecorder.exe`) and send START and STOP
from LabVIEW. LabVIEW then needs no Python and no VLC.

## TCP remote control from LabVIEW
Functions palette → **Data Communication → Protocols → TCP**

```
TCP Open Connection  (address "127.0.0.1" or the recorder PC's IP, port 5000, timeout 5000 ms)
   → TCP Write  "START;C:\Recordings;Test_001" + CR LF     (use the "\r\n" constant, or Concatenate + CR + LF)
   → TCP Read   bytes to read 512, mode = CRLF, timeout 10000 ms
        reply = "OK START;C:\Recordings\Test_001.mp4"
   ... later ...
   → TCP Write  "STOP" + CR LF
   → TCP Read   (CRLF)  reply = "OK STOP;C:\Recordings\Test_001.mp4"
TCP Close Connection
```

- You can keep one connection open for the whole test, or open and close it for every command.
- Check the reply: **Match Pattern** / **Scan From String** on `OK ` or `ERROR `. The part after `;` is the file path.
- If the recorder runs on another PC, allow the app through Windows Firewall when
  Windows asks the first time. Use port 5000, or the port set in the app.
- Every command and reply is shown in the app's log box. This helps when debugging.

| Command | Reply |
|---|---|
| `PING` | `OK PONG` |
| `START;<folder>;<file name>` | `OK START;<path>`. Both fields are optional, so `START` alone is fine |
| `STOP` | `OK STOP;<path>`. It is sent after the file is closed |
| `STATUS` | `OK RECORDING;<path>` / `OK IDLE` / `OK WAITING_MOTION` / `OK CONNECTING` / `OK DISCONNECTED` |
| `SNAPSHOT;<folder>;<file name>` | `OK SNAPSHOT;<path>` |
| `CONNECT` / `DISCONNECT` | `OK CONNECT` / `OK DISCONNECT` |

---

# Python Node (no separate app)

LabVIEW calls the Python functions in `labview_camera.py` with the **Python Node**
(LabVIEW 2018 or newer). LabVIEW provides the buttons and display, and Python
does the RTSP reading and recording in the background.

## 1. One-time setup
1. Install Python from python.org, **not** the Microsoft Store version.
   - Use the same bitness as LabVIEW (64-bit LabVIEW needs 64-bit Python).
   - Use a Python version your LabVIEW supports. Check *LabVIEW Help → Python Node → supported versions*.
2. Install the packages into **that** Python:
   ```
   py -3.x -m pip install opencv-python numpy
   ```
3. Keep `labview_camera.py` and `camera_core.py` in the same folder.
4. Test without LabVIEW:
   ```
   py -3.x labview_camera.py rtsp://admin:admin@192.168.1.126:554/unicaststream/1
   ```

## 2. Block diagram
Functions palette → **Connectivity → Python**

```
Open Python Session (version "3.x")
   │
   ├─ Python Node: connect(url)                         → String
   │
   ├─ While Loop (100–200 ms wait) ───────────────────────────────┐
   │     Python Node: get_status()        → String  → indicator   │
   │     Python Node: get_frame(640)      → 2D U32  → Draw Unflattened Pixmap → Picture indicator
   │     Event/case on buttons:                                   │
   │        Record  → start_recording(folder, 10, motion?) → String
   │        Stop    → stop_recording()                     → String (saved file)
   │        Snap    → snapshot(folder)                     → String (jpg path)
   └──────────────────────────────────────────────────────────────┘
   │
   ├─ Python Node: disconnect()                         → String
Close Python Session
```

How to set up each Python Node:
- **module path**: full path to `labview_camera.py`
- **function name**: e.g. `get_frame`
- **return type**: wire a constant of the right type to the *return type* terminal
  (String constant, Boolean constant, or a **2D array of U32** for `get_frame`)
- **inputs**: extend the node and wire the arguments in order

Wire the same **session refnum** through every node. The camera thread lives
in that session. If you close the session, the recording stops.

## 3. Showing the video
`get_frame(width)` returns a 2D U32 array (each pixel is 0x00RRGGBB):
**Graphics & Sound → Picture Functions → Draw Unflattened Pixmap**
(wire the array to *data*, leave *depth* = 24) → Picture indicator.

`get_frame` copies the whole image into LabVIEW, so keep the width at 640 or
below. For a faster or larger preview, use `save_preview("C:\\temp\\live.jpg", 960)`
followed by **Read JPEG File → Draw Flattened Pixmap**.

## Function reference
| Function | Inputs | Returns |
|---|---|---|
| `connect` | url (String) | String |
| `disconnect` | — | String |
| `start_recording` | save_dir (String), split_minutes (I32), motion_only (Boolean), file_name (String) | String: file path |
| `stop_recording` | — | String: saved file path |
| `snapshot` | save_dir (String) | String: jpg path |
| `get_status` | — | String |
| `is_connected` / `is_recording` | — | Boolean |
| `get_frame` | width (I32) | 2D array of U32 |
| `save_preview` | path (String), width (I32) | String |

## Alternative: run the .exe from LabVIEW
If you only need to launch the desktop app, use **System Exec.vi** with
`C:\path\IPCameraRecorder.exe` and set **Connect & record when app starts**
in the app.

---

# VLC + LabVIEW: watch and record

You can use VLC instead of Python. VLC shows the camera on the front panel, and
`labview/vlc_record.ps1` records in the background. Both copy the camera's H.264
stream without re-encoding, so CPU load stays low and the quality matches the camera.

## 1. Setup
1. Install **VLC 3.x** with the **ActiveX plugin** component ticked. Use the same bitness as LabVIEW.
2. Copy `labview\vlc_record.ps1` next to your VI.

## 2. Live view on the front panel (VLC ActiveX)
1. Front panel: **Containers → ActiveX Container**. Right-click it → *Insert ActiveX Object* →
   **VideoLAN VLC ActiveX Plugin v2**.
2. Block diagram, using the container's reference:
   - Property Node → `playlist` → Invoke Node `items.clear`
   - Invoke Node `add`:
     - uri = `rtsp://admin:admin@192.168.1.126:554/unicaststream/2` (the sub stream is enough for viewing)
     - name = empty
     - options = string array `[":rtsp-tcp", ":network-caching=300"]`
   - Invoke Node `playItem` with the id that `add` returned
3. When the VI stops, call `playlist.stop`.

## 3. Recording (System Exec.vi)
**Connectivity → Libraries & Executables → System Exec.vi**

| Button | command line | wait until completion? |
|---|---|---|
| Start | `powershell -NoProfile -ExecutionPolicy Bypass -File "C:\path\vlc_record.ps1" start -OutDir "C:\CameraRecordings"` | **True** (returns in about 3 s) |
| Stop | `powershell -NoProfile -ExecutionPolicy Bypass -File "C:\path\vlc_record.ps1" stop -OutDir "C:\CameraRecordings"` | True |
| Status | `... vlc_record.ps1 status -OutDir "C:\CameraRecordings"` | True |

- **standard output** of *start* is the path of the new file. Wire it to a string indicator.
  A message starting with `ERROR:` means something failed.
- The script records the main stream (`/unicaststream/1`, 1080p) by default.
  Change it with `-Url "rtsp://..."`.
- `-Minutes 10` makes VLC stop by itself after 10 minutes. For files that split
  continuously, call Stop and then Start again every N minutes from a LabVIEW timer.
- Recordings are `.ts` files, which stay playable even if the PC loses power. VLC and
  most players open them.
- The script stops only the VLC process it started. The front-panel ActiveX view and
  any other VLC windows keep running.

## Recording from the ActiveX view instead
You can also record from the viewer itself: re-add the URL with a third option
`:sout=#duplicate{dst=display,dst=std{access=file,mux=ts,dst='C:\CameraRecordings\cam.ts'}}`
and call `playItem`. To stop, re-add the URL without `:sout`. The picture freezes for
about 1 s each time, so the System Exec method above is usually smoother.
