# Using the IP camera recorder from LabVIEW

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
| `start_recording` | save_dir (String), split_minutes (I32), motion_only (Boolean) | String |
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
