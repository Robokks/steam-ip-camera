"""
LabVIEW API for the IP camera recorder.

Call these functions from LabVIEW's Python Node
(Functions palette → Connectivity → Python). Every function takes and
returns simple types (strings, numbers, booleans, 2D U32 arrays) that the
Python Node can map directly.

The camera runs on a background thread inside the Python session, so keep
ONE Python session open for the whole VI run:

    Open Python Session → connect → [loop: get_status / get_frame / ...]
                        → disconnect → Close Python Session

Functions                                   LabVIEW return type
    connect(url)                            String
    disconnect()                            String
    start_recording(save_dir, split_minutes, motion_only, file_name)   String (file path)
    stop_recording()                        String  (path of last saved file)
    snapshot(save_dir)                      String  (path of the JPEG)
    get_status()                            String
    is_connected()                          Boolean
    is_recording()                          Boolean (True only while a file is being written)
    get_frame(width)                        2D array of U32 (0x00RRGGBB) → Draw Unflattened Pixmap
    save_preview(path, width)               String  (JPEG path) → Read JPEG File
"""

import os
import sys
import time
from datetime import datetime

# Make sibling modules importable when LabVIEW loads this file by path.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from camera_core import CameraWorker  # noqa: E402

_worker = None


def connect(url):
    """Start reading the camera. Returns immediately; poll get_status()."""
    global _worker
    disconnect()
    _worker = CameraWorker(str(url))
    _worker.start()
    return "Connecting..."


def disconnect():
    global _worker
    if _worker is None:
        return "Not connected"
    _worker.stop()
    _worker.join(timeout=5)
    saved = _worker.last_saved
    _worker = None
    return f"Disconnected. Last file: {saved}" if saved else "Disconnected"


def start_recording(save_dir, split_minutes=10, motion_only=False, file_name=""):
    """Start recording into save_dir. Returns the file path that will be written.

    split_minutes=0 means one file. file_name is optional (no extension needed).
    """
    if _worker is None:
        return "ERROR: not connected"
    _worker.split_minutes = max(0, int(split_minutes))
    _worker.motion_only = bool(motion_only)
    return _worker.start_recording(str(save_dir), str(file_name or ""))


def stop_recording():
    """Stop recording. Returns the path of the file that was saved."""
    if _worker is None:
        return ""
    return _worker.stop_recording()


def snapshot(save_dir):
    frame = _worker.latest_frame() if _worker else None
    if frame is None:
        return "ERROR: no video"
    os.makedirs(str(save_dir), exist_ok=True)
    path = os.path.join(str(save_dir), f"snap_{datetime.now():%Y%m%d_%H%M%S}.jpg")
    cv2.imwrite(path, frame)
    return path


def get_status():
    if _worker is None:
        return "Not connected"
    status = _worker.status
    if _worker.writing:
        status += f" | REC {os.path.basename(_worker.current_file)}"
    elif _worker.record_on:
        status += " | waiting for motion" if _worker.motion_only else " | starting recording"
    return status


def is_connected():
    return bool(_worker and _worker.connected)


def is_recording():
    return bool(_worker and _worker.writing)


def _latest_resized(width):
    frame = _worker.latest_frame() if _worker else None
    if frame is None:
        return None
    h, w = frame.shape[:2]
    width = int(width) if width and int(width) > 0 else w
    if width != w:
        frame = cv2.resize(frame, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
    return frame


def get_frame(width=640):
    """Latest frame as a 2D U32 array (0x00RRGGBB), for Draw Unflattened Pixmap.

    Keep width small (e.g. 640) — large arrays are slow to pass to LabVIEW.
    Returns a 1x1 black image when no video is available.
    """
    frame = _latest_resized(width)
    if frame is None:
        return [[0]]
    f = frame.astype(np.uint32)
    rgb = (f[:, :, 2] << 16) | (f[:, :, 1] << 8) | f[:, :, 0]
    return rgb.tolist()


def save_preview(path, width=640):
    """Write the latest frame to a JPEG (faster alternative to get_frame)."""
    frame = _latest_resized(width)
    if frame is None:
        return "ERROR: no video"
    cv2.imwrite(str(path), frame)
    return str(path)


if __name__ == "__main__":
    # Quick self-test without LabVIEW: python labview_camera.py <rtsp-url>
    url = sys.argv[1] if len(sys.argv) > 1 else "rtsp://admin:admin@192.168.1.126:554/unicaststream/1"
    print(connect(url))
    for _ in range(50):
        if is_connected():
            break
        time.sleep(0.2)
    print(get_status())
    print(start_recording("recordings", 1, False, "labview_test"))
    time.sleep(5)
    print("Saved:", stop_recording())
    print("Snapshot:", snapshot("recordings"))
    frame = get_frame(320)
    print(f"get_frame: {len(frame)}x{len(frame[0])}")
    print(disconnect())
