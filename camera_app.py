"""
IP Camera Recorder — Windows desktop app.

Live preview of an RTSP IP camera with recording:
  * manual start/stop, or auto-record on connect
  * motion-only recording (with pre-roll)
  * auto split into N-minute files
  * snapshots, timestamp overlay, auto-reconnect
  * TCP remote control (START/STOP with folder + file name) — see tcp_server.py

Settings are saved to %APPDATA%\\IPCameraRecorder\\settings.json.
Build an .exe with build_exe.bat (PyInstaller).
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from camera_core import CameraWorker, clean_filename
from tcp_server import CommandServer, parse_command

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
    "file_name": "",
    "tcp_enabled": True,
    "tcp_port": 5000,
}

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
#  GUI
# ─────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.minsize(1000, 700)
        self.settings = load_settings()
        self.worker = None
        self._photo = None
        self.server = None
        self._commands = queue.Queue()     # TCP thread -> GUI thread
        self._logs = queue.Queue()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(40, self._refresh)
        if self.settings["auto_record"] and self.settings["password"]:
            self.after(300, self.connect)
        if self.settings["tcp_enabled"]:
            self.after(200, self.start_server)

    # ---- layout ----
    def _build_ui(self):
        s = self.settings
        self.v_status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.v_status, relief=tk.SUNKEN, anchor="w",
                  padding=(6, 2)).pack(side=tk.BOTTOM, fill=tk.X)

        self._build_tcp_panel()

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
        self.v_name = tk.StringVar(value=s["file_name"])
        ttk.Label(rec, text="File name (empty = date/time)").grid(row=2, column=0, columnspan=2,
                                                                  sticky="w", pady=(6, 0))
        ttk.Entry(rec, textvariable=self.v_name, width=28).grid(row=3, column=0, columnspan=2, sticky="ew")
        ttk.Label(rec, text="New file every (min)").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(rec, from_=0, to=1440, textvariable=self.v_split, width=6,
                    command=self._apply_options).grid(row=4, column=1, pady=(6, 0))
        ttk.Checkbutton(rec, text="Record only when motion detected", variable=self.v_motion,
                        command=self._apply_options).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(rec, text="Show date/time on video", variable=self.v_stamp,
                        command=self._apply_options).grid(row=6, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(rec, text="Connect & record when app starts", variable=self.v_auto,
                        ).grid(row=7, column=0, columnspan=2, sticky="w")

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

    def _build_tcp_panel(self):
        s = self.settings
        box = ttk.LabelFrame(self, text="Remote control (TCP)", padding=8)
        box.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 6))
        left = ttk.Frame(box)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.v_tcp = tk.BooleanVar(value=s["tcp_enabled"])
        self.v_tcp_port = tk.StringVar(value=str(s["tcp_port"]))
        ttk.Checkbutton(left, text="Enable TCP server", variable=self.v_tcp,
                        command=self._tcp_toggled).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(left, text="Port").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(left, textvariable=self.v_tcp_port, width=8).grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.lbl_tcp = ttk.Label(left, text="Server off", foreground="gray", width=34)
        self.lbl_tcp.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(left, text="Commands: START;folder;name  STOP  STATUS  SNAPSHOT  PING",
                  foreground="gray", wraplength=260).grid(row=3, column=0, columnspan=2, sticky="w")
        self.log = tk.Text(box, height=6, state=tk.DISABLED, font=("Consolas", 9))
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))


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
            file_name=self.v_name.get().strip(), tcp_enabled=self.v_tcp.get(),
        )
        try:
            self.settings["tcp_port"] = int(self.v_tcp_port.get())
        except ValueError:
            pass
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
        if s["auto_record"]:
            self.worker.start_recording(s["save_dir"], s["file_name"])
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
        if self.worker.record_on:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self, folder="", name=""):
        """Manual button or remote START. Returns the file path."""
        s = self._collect_settings()
        if not self.worker:
            self.connect()
        self._apply_options()
        return self.worker.start_recording(folder or s["save_dir"], name or s["file_name"])

    def stop_recording(self):
        """Stop recording (non-blocking for the GUI). Returns the current file path."""
        if not self.worker or not self.worker.record_on:
            return ""
        path = self.worker.current_file
        self.worker.record_on = False
        return path

    def snapshot(self, folder="", name=""):
        frame = self.worker.latest_frame() if self.worker else None
        if frame is None:
            return ""
        d = folder or self._collect_settings()["save_dir"]
        os.makedirs(d, exist_ok=True)
        base = clean_filename(name) if name else ""
        path = os.path.join(d, (base or f"snap_{datetime.now():%Y%m%d_%H%M%S}") + ".jpg")
        cv2.imwrite(path, frame)
        self.v_status.set(f"Snapshot saved: {path}")
        return path

    # ---- TCP remote control ----
    def _log(self, text):
        """Thread-safe: queue a line for the log box."""
        self._logs.put(f"{datetime.now():%H:%M:%S}  {text}")

    def _tcp_toggled(self):
        if self.v_tcp.get():
            self.start_server()
        else:
            self.stop_server()

    def start_server(self):
        self.stop_server()
        s = self._collect_settings()
        self.server = CommandServer(self._handle_remote, port=s["tcp_port"], log=self._log)
        self.server.start()

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
            self._log("TCP server stopped")

    def _handle_remote(self, line):
        """Runs on a TCP client thread: hand the command to the GUI thread and wait."""
        done = threading.Event()
        result = {}
        self._commands.put((line, result, done))
        if not done.wait(15):
            return "ERROR TIMEOUT"
        reply = result.get("reply", "ERROR")
        # For STOP, wait (off the GUI thread) until the file is really closed.
        if reply.startswith("OK STOP") and self.worker:
            deadline = time.time() + 3
            while self.worker and self.worker.writing and time.time() < deadline:
                time.sleep(0.02)
        return reply

    def _execute(self, line):
        """Runs on the GUI thread."""
        cmd, args = parse_command(line)
        arg = (args + ["", ""])[:2]
        w = self.worker
        if cmd == "PING":
            return "OK PONG"
        if cmd == "START":
            if w and w.record_on:
                return f"ERROR ALREADY RECORDING;{w.current_file}"
            return "OK START;" + self.start_recording(arg[0], arg[1])
        if cmd == "STOP":
            if not w or not w.record_on:
                return "ERROR NOT RECORDING"
            return "OK STOP;" + (self.stop_recording() or w.last_saved)
        if cmd == "STATUS":
            if not w:
                return "OK DISCONNECTED"
            if w.writing:
                return f"OK RECORDING;{w.current_file}"
            if w.record_on:
                return "OK WAITING_MOTION" if w.motion_only else "OK STARTING"
            return "OK IDLE" if w.connected else "OK CONNECTING"
        if cmd == "SNAPSHOT":
            path = self.snapshot(arg[0], arg[1])
            return f"OK SNAPSHOT;{path}" if path else "ERROR NO VIDEO"
        if cmd == "CONNECT":
            if not w:
                self.connect()
            return "OK CONNECT"
        if cmd == "DISCONNECT":
            self.disconnect()
            return "OK DISCONNECT"
        return f"ERROR UNKNOWN COMMAND {cmd}"

    def _process_remote(self):
        while True:
            try:
                line, result, done = self._commands.get_nowait()
            except queue.Empty:
                break
            try:
                result["reply"] = self._execute(line)
            except Exception as e:  # noqa: BLE001
                result["reply"] = f"ERROR {e}"
            done.set()
        lines = []
        while True:
            try:
                lines.append(self._logs.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log.config(state=tk.NORMAL)
            self.log.insert(tk.END, "\n".join(lines) + "\n")
            if int(self.log.index("end-1c").split(".")[0]) > 500:
                self.log.delete("1.0", "100.0")
            self.log.see(tk.END)
            self.log.config(state=tk.DISABLED)
        srv = self.server
        if srv is None:
            self.lbl_tcp.config(text="Server off", foreground="gray")
        elif srv.error:
            self.lbl_tcp.config(text=srv.error, foreground="red")
        else:
            self.lbl_tcp.config(text=f"Listening on port {srv.port}  —  {srv.clients} client(s)",
                                foreground="green")

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
        self._process_remote()
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
        self.stop_server()
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
