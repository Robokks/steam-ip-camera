"""
IP Camera Recorder — Windows desktop app.

Live preview of an RTSP IP camera with recording:
  * manual start/stop, or auto-record on connect
  * motion-only recording (with pre-roll)
  * auto split into N-minute files
  * snapshots, timestamp overlay, auto-reconnect

Settings are saved to %APPDATA%\\IPCameraRecorder\\settings.json.
Build an .exe with build_exe.bat (PyInstaller).
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

APP_NAME = "IP Camera Recorder"
CONFIG_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "IPCameraRecorder")
CONFIG_PATH = os.path.join(CONFIG_DIR, "settings.json")

STREAMS = {
    "Main stream (1080p)": "/unicaststream/1",
    "Sub stream (low res)": "/unicaststream/2",
}

DEFAULTS = {
    "ip": "192.168.1.126",
    "port": "554",
    "user": "admin",
    "password": "",
    "stream": "Main stream (1080p)",
    "custom_url": "",
    "save_dir": os.path.join(os.path.expanduser("~"), "Videos", "IPCamera"),
    "split_minutes": 10,
    "motion_only": False,
    "timestamp": True,
    "auto_record": False,
}

os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;5000000")


def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


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


# ─────────────────────────────────────────────
#  GUI
# ─────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x700")
        self.minsize(900, 550)
        self.settings = load_settings()
        self.worker = None
        self._photo = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self._refresh)
        if self.settings["auto_record"] and self.settings["password"]:
            self.after(300, self.connect)

    # ---- layout ----
    def _build_ui(self):
        s = self.settings
        self.v_status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.v_status, relief=tk.SUNKEN, anchor="w",
                  padding=(6, 2)).pack(side=tk.BOTTOM, fill=tk.X)

        side = ttk.Frame(self, padding=10)
        side.pack(side=tk.LEFT, fill=tk.Y)

        cam = ttk.LabelFrame(side, text="Camera", padding=8)
        cam.pack(fill=tk.X)
        self.v_ip = tk.StringVar(value=s["ip"])
        self.v_port = tk.StringVar(value=s["port"])
        self.v_user = tk.StringVar(value=s["user"])
        self.v_pass = tk.StringVar(value=s["password"])
        self.v_stream = tk.StringVar(value=s["stream"])
        self.v_custom = tk.StringVar(value=s["custom_url"])
        rows = [("IP address", ttk.Entry(cam, textvariable=self.v_ip, width=24)),
                ("RTSP port", ttk.Entry(cam, textvariable=self.v_port, width=24)),
                ("Username", ttk.Entry(cam, textvariable=self.v_user, width=24)),
                ("Password", ttk.Entry(cam, textvariable=self.v_pass, width=24, show="•")),
                ("Stream", ttk.Combobox(cam, textvariable=self.v_stream, values=list(STREAMS),
                                        state="readonly", width=22))]
        for r, (label, widget) in enumerate(rows):
            ttk.Label(cam, text=label).grid(row=r, column=0, sticky="w", pady=2)
            widget.grid(row=r, column=1, sticky="ew", pady=2)
        ttk.Label(cam, text="Custom URL (optional)").grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Entry(cam, textvariable=self.v_custom, width=36).grid(row=6, column=0, columnspan=2, sticky="ew")

        self.btn_connect = ttk.Button(side, text="▶  Connect", command=self.toggle_connect)
        self.btn_connect.pack(fill=tk.X, pady=(8, 0))

        rec = ttk.LabelFrame(side, text="Recording", padding=8)
        rec.pack(fill=tk.X, pady=(10, 0))
        self.v_dir = tk.StringVar(value=s["save_dir"])
        self.v_split = tk.IntVar(value=s["split_minutes"])
        self.v_motion = tk.BooleanVar(value=s["motion_only"])
        self.v_stamp = tk.BooleanVar(value=s["timestamp"])
        self.v_auto = tk.BooleanVar(value=s["auto_record"])

        ttk.Label(rec, text="Save folder").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(rec, textvariable=self.v_dir, width=28).grid(row=1, column=0, sticky="ew")
        ttk.Button(rec, text="…", width=3, command=self.browse).grid(row=1, column=1, padx=(4, 0))
        ttk.Label(rec, text="New file every (min)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(rec, from_=0, to=1440, textvariable=self.v_split, width=6,
                    command=self._apply_options).grid(row=2, column=1, pady=(6, 0))
        ttk.Checkbutton(rec, text="Record only when motion detected", variable=self.v_motion,
                        command=self._apply_options).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(rec, text="Show date/time on video", variable=self.v_stamp,
                        command=self._apply_options).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(rec, text="Connect & record when app starts", variable=self.v_auto,
                        ).grid(row=5, column=0, columnspan=2, sticky="w")

        self.btn_record = ttk.Button(side, text="⏺  Start recording", command=self.toggle_record,
                                     state=tk.DISABLED)
        self.btn_record.pack(fill=tk.X, pady=(8, 0))
        self.btn_snap = ttk.Button(side, text="📷  Snapshot", command=self.snapshot, state=tk.DISABLED)
        self.btn_snap.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(side, text="📂  Open recordings folder", command=self.open_folder).pack(fill=tk.X, pady=(4, 0))

        self.lbl_rec = ttk.Label(side, text="", foreground="red", wraplength=260)
        self.lbl_rec.pack(fill=tk.X, pady=(10, 0))

        self.video = tk.Label(self, bg="black", fg="gray", text="Not connected",
                              font=("Segoe UI", 16))
        self.video.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)


    # ---- helpers ----
    def _collect_settings(self):
        try:
            split = int(self.v_split.get())
        except (tk.TclError, ValueError):
            split = DEFAULTS["split_minutes"]
        self.settings.update(
            ip=self.v_ip.get().strip(), port=self.v_port.get().strip() or "554",
            user=self.v_user.get().strip(), password=self.v_pass.get(),
            stream=self.v_stream.get(), custom_url=self.v_custom.get().strip(),
            save_dir=self.v_dir.get().strip() or DEFAULTS["save_dir"], split_minutes=max(0, split),
            motion_only=self.v_motion.get(), timestamp=self.v_stamp.get(), auto_record=self.v_auto.get(),
        )
        return self.settings

    def _url(self):
        s = self.settings
        if s["custom_url"]:
            return s["custom_url"]
        path = STREAMS.get(s["stream"], "/unicaststream/1")
        cred = f"{s['user']}:{s['password']}@" if s["user"] else ""
        return f"rtsp://{cred}{s['ip']}:{s['port']}{path}"

    def _apply_options(self):
        s = self._collect_settings()
        if self.worker:
            self.worker.motion_only = s["motion_only"]
            self.worker.timestamp = s["timestamp"]
            self.worker.split_minutes = s["split_minutes"]
            self.worker.save_dir = s["save_dir"]

    # ---- actions ----
    def toggle_connect(self):
        if self.worker:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        s = self._collect_settings()
        save_settings(s)
        self.worker = CameraWorker(self._url())
        self._apply_options()
        self.worker.record_on = s["auto_record"]
        self.worker.start()
        self.btn_connect.config(text="⏹  Disconnect")
        self.btn_record.config(state=tk.NORMAL)
        self.btn_snap.config(state=tk.NORMAL)
        self.video.config(image="", text="Connecting...")

    def disconnect(self):
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=5)
            if self.worker.last_saved:
                self.v_status.set(f"Saved: {self.worker.last_saved}")
            self.worker = None
        self._photo = None
        self.video.config(image="", text="Not connected")
        self.btn_connect.config(text="▶  Connect")
        self.btn_record.config(state=tk.DISABLED, text="⏺  Start recording")
        self.btn_snap.config(state=tk.DISABLED)
        self.lbl_rec.config(text="")

    def toggle_record(self):
        if not self.worker:
            return
        self._apply_options()
        self.worker.record_on = not self.worker.record_on

    def snapshot(self):
        frame = self.worker.latest_frame() if self.worker else None
        if frame is None:
            return
        d = self._collect_settings()["save_dir"]
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"snap_{datetime.now():%Y%m%d_%H%M%S}.jpg")
        cv2.imwrite(path, frame)
        self.v_status.set(f"Snapshot saved: {path}")

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.v_dir.get() or os.path.expanduser("~"))
        if d:
            self.v_dir.set(d)
            self._apply_options()

    def open_folder(self):
        d = self._collect_settings()["save_dir"]
        os.makedirs(d, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(d)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", d])

    # ---- periodic UI update ----
    def _refresh(self):
        w = self.worker
        if w:
            frame = w.latest_frame()
            if frame is not None:
                self._show(frame)
            self.v_status.set(w.status)
            if w.record_on:
                self.btn_record.config(text="⏹  Stop recording")
                if w.writing:
                    blink = "●" if int(time.time() * 2) % 2 else "○"
                    self.lbl_rec.config(text=f"{blink} REC  {os.path.basename(w.current_file)}",
                                        foreground="red")
                else:
                    self.lbl_rec.config(text="Waiting for motion..." if w.motion_only else "Waiting for video...",
                                        foreground="dark orange")
            else:
                self.btn_record.config(text="⏺  Start recording")
                self.lbl_rec.config(text=f"Last file: {os.path.basename(w.last_saved)}" if w.last_saved else "",
                                    foreground="gray")
        self.after(40, self._refresh)

    def _show(self, frame):
        box_w, box_h = max(self.video.winfo_width(), 1), max(self.video.winfo_height(), 1)
        h, w = frame.shape[:2]
        scale = min(box_w / w, box_h / h)
        if scale <= 0:
            return
        frame = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self._photo = ImageTk.PhotoImage(img)
        self.video.config(image=self._photo, text="")

    def on_close(self):
        if self.worker and self.worker.writing:
            if not messagebox.askyesno(APP_NAME, "Recording is in progress. Stop and exit?"):
                return
        save_settings(self._collect_settings())
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
