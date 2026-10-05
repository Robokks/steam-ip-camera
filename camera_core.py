"""
Camera core: background RTSP reader + recorder, shared by the desktop app
(camera_app.py) and the LabVIEW API (labview_camera.py).
"""

import os
import threading
import time
from collections import deque
from datetime import datetime

import cv2

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;5000000")


# ─────────────────────────────────────────────
#  Background worker: reads the camera and writes video files
# ─────────────────────────────────────────────
class CameraWorker(threading.Thread):
    RECONNECT_SEC = 3
    POST_MOTION_SEC = 10
    PRE_MOTION_SEC = 3
    MIN_AREA = 5000          # pixels in the full-size frame
    WARMUP_FRAMES = 50

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame = None

        # Options set from the GUI thread (simple attribute writes are safe).
        self.record_on = False
        self.motion_only = False
        self.timestamp = True
        self.split_minutes = 10
        self.save_dir = "."

        # Status read by the GUI thread.
        self.status = "Connecting..."
        self.connected = False
        self.writing = False
        self.current_file = ""
        self.last_saved = ""

        self._writer = None
        self._file_started = 0.0
        self._fps = 25.0
        self._size = None
        self._reset_motion()

    # ---- public API (called from GUI thread) ----
    def latest_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self):
        self._stop_event.set()

    # ---- thread body ----
    def run(self):
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self.connected = False
                self.status = f"Cannot connect — retrying in {self.RECONNECT_SEC}s"
                self._stop_event.wait(self.RECONNECT_SEC)
                continue

            fps = cap.get(cv2.CAP_PROP_FPS)
            self._fps = fps if 1 <= fps <= 60 else 25.0
            self._reset_motion()
            got_frame = False

            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                if not got_frame:
                    h, w = frame.shape[:2]
                    self._size = (w, h)
                    self.connected = True
                    self.status = f"Connected  {w}x{h} @ {self._fps:.0f} fps"
                    got_frame = True
                if self.timestamp:
                    draw_timestamp(frame)
                with self._lock:
                    self._frame = frame
                self._handle_recording(frame)

            cap.release()
            self._close_writer()
            self.connected = False
            if not self._stop_event.is_set():
                self.status = f"Connection lost — reconnecting in {self.RECONNECT_SEC}s"
                self._stop_event.wait(self.RECONNECT_SEC)

        self._close_writer()
        self.status = "Disconnected"

    # ---- recording ----
    def _reset_motion(self):
        self._bg = None
        self._motion_frames = 0
        self._last_motion = 0.0
        self._pre_buffer = deque()

    def _detect_motion(self, frame):
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        small = cv2.GaussianBlur(small, (5, 5), 0)
        if self._bg is None:
            self._bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25)
            self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = self._bg.apply(small)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)   # drop shadows
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        self._motion_frames += 1
        if self._motion_frames <= self.WARMUP_FRAMES:
            return False
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = self.MIN_AREA * scale * scale
        return any(cv2.contourArea(c) > min_area for c in contours)

    def _handle_recording(self, frame):
        now = time.time()
        if self.motion_only:
            if self._detect_motion(frame):
                self._last_motion = now
            want = self.record_on and self._last_motion and now - self._last_motion <= self.POST_MOTION_SEC
        else:
            want = self.record_on

        if want and self._writer is None:
            self._open_writer()
            while self._pre_buffer:
                self._writer.write(self._pre_buffer.popleft())
        elif not want and self._writer is not None:
            self._close_writer()

        if self._writer is not None:
            if self.split_minutes and now - self._file_started >= self.split_minutes * 60:
                self._close_writer()
                self._open_writer()
            self._writer.write(frame)
        elif self.motion_only and self.record_on:
            maxlen = int(self.PRE_MOTION_SEC * self._fps)
            self._pre_buffer.append(frame)
            while len(self._pre_buffer) > maxlen:
                self._pre_buffer.popleft()

    def _open_writer(self):
        os.makedirs(self.save_dir, exist_ok=True)
        prefix = "motion" if self.motion_only else "cam"
        path = os.path.join(self.save_dir, f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.mp4")
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), self._fps, self._size)
        if not writer.isOpened():
            self.status = f"ERROR: cannot write to {self.save_dir}"
            self.record_on = False
            return
        self._writer = writer
        self._file_started = time.time()
        self.current_file = path
        self.writing = True

    def _close_writer(self):
        if self._writer is not None:
            self._writer.release()
            self._writer = None
            self.last_saved = self.current_file
            self.current_file = ""
        self.writing = False


def draw_timestamp(frame):
    text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scale = max(0.6, frame.shape[1] / 1600)
    org = (10, frame.shape[0] - 15)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1, cv2.LINE_AA)
