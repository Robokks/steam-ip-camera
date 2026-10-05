#!/usr/bin/env python3
"""IP camera viewer and recorder.

Reads a live feed from an IP camera (RTSP / HTTP MJPEG / any URL or file
OpenCV can open), shows it in a window, and records it to video files.

Keyboard controls (in the video window):
    r  start / stop recording
    s  save a snapshot (JPEG)
    q  quit (Esc also works)

Examples:
    python ip_camera_recorder.py rtsp://user:pass@192.168.1.10:554/stream1
    python ip_camera_recorder.py http://192.168.1.20:8080/video --record
    python ip_camera_recorder.py rtsp://... --record --no-display --segment 600
"""

import argparse
import logging
import os
import signal
import threading
import time
from datetime import datetime

import cv2

log = logging.getLogger("ipcam")

CODECS = {
    ".mp4": "mp4v",
    ".avi": "XVID",
    ".mkv": "XVID",
}


class CameraStream:
    """Grabs frames on a background thread so the newest frame is always ready.

    Reading on a separate thread stops RTSP buffers from filling up (which
    causes growing lag) and lets the stream reconnect if the camera drops.
    """

    def __init__(self, source, reconnect_delay=2.0):
        self.source = int(source) if str(source).isdigit() else source
        self.reconnect_delay = reconnect_delay
        self.cap = None
        self.frame = None
        self.frame_id = 0
        self.fps = 0.0
        self.connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _open(self):
        if isinstance(self.source, str) and self.source.startswith("rtsp"):
            # TCP is far more reliable than UDP for most cameras.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return False
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if 1 <= fps <= 120 else 0.0
        self.cap = cap
        self.connected = True
        log.info("Connected to %s (reported FPS: %s)", self._safe_source(), self.fps or "unknown")
        return True

    def _run(self):
        is_file = isinstance(self.source, str) and os.path.isfile(self.source)
        while not self._stop.is_set():
            if self.cap is None and not self._open():
                log.warning("Cannot open %s, retrying in %.0fs", self._safe_source(), self.reconnect_delay)
                self._stop.wait(self.reconnect_delay)
                continue

            ok, frame = self.cap.read()
            if not ok:
                if is_file:
                    log.info("End of file reached")
                    self.connected = False
                    self._stop.set()
                    break
                log.warning("Lost connection, reconnecting...")
                self.connected = False
                self.cap.release()
                self.cap = None
                self._stop.wait(self.reconnect_delay)
                continue

            with self._lock:
                self.frame = frame
                self.frame_id += 1
            if is_file and self.fps:
                time.sleep(1.0 / self.fps)  # play files at real speed

        if self.cap is not None:
            self.cap.release()

    def read(self):
        """Return (frame_id, frame copy) of the latest frame, or (id, None)."""
        with self._lock:
            if self.frame is None:
                return self.frame_id, None
            return self.frame_id, self.frame.copy()

    @property
    def finished(self):
        return self._stop.is_set()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _safe_source(self):
        """Source string with any password hidden, for logging."""
        s = str(self.source)
        if "@" in s and "://" in s:
            scheme, rest = s.split("://", 1)
            creds, host = rest.rsplit("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
        return s


class Recorder:
    """Writes frames to video files, optionally splitting into fixed-length segments."""

    def __init__(self, out_dir, fps, ext=".mp4", segment_seconds=0, prefix="cam"):
        self.out_dir = out_dir
        self.fps = fps
        self.ext = ext if ext.startswith(".") else "." + ext
        self.segment_seconds = segment_seconds
        self.prefix = prefix
        self.writer = None
        self.path = None
        self.started_at = 0.0
        self.frames_written = 0
        os.makedirs(out_dir, exist_ok=True)

    @property
    def active(self):
        return self.writer is not None

    def start(self, frame_size):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(self.out_dir, f"{self.prefix}_{stamp}{self.ext}")
        fourcc = cv2.VideoWriter_fourcc(*CODECS.get(self.ext, "mp4v"))
        self.writer = cv2.VideoWriter(self.path, fourcc, self.fps, frame_size)
        if not self.writer.isOpened():
            self.writer = None
            raise RuntimeError(f"Could not open video writer for {self.path}")
        self.started_at = time.time()
        self.frames_written = 0
        log.info("Recording started: %s (%dx%d @ %.1f fps)", self.path, *frame_size, self.fps)

    def write(self, frame):
        if not self.active:
            return
        h, w = frame.shape[:2]
        if self.segment_seconds and time.time() - self.started_at >= self.segment_seconds:
            self.stop()
            self.start((w, h))
        self.writer.write(frame)
        self.frames_written += 1

    def stop(self):
        if self.writer is None:
            return
        self.writer.release()
        self.writer = None
        log.info("Recording saved: %s (%d frames, %.0fs)",
                 self.path, self.frames_written, time.time() - self.started_at)

    def toggle(self, frame_size):
        if self.active:
            self.stop()
        else:
            self.start(frame_size)


def draw_timestamp(frame, show_timestamp):
    """Burn the current date/time into the frame (this goes into the recording)."""
    if show_timestamp:
        text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, text, (10, frame.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, text, (10, frame.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def draw_status(frame, recording, connected):
    """Status badges shown only on screen (never written to the recording)."""
    if recording and int(time.time() * 2) % 2 == 0:
        cv2.circle(frame, (20, 20), 8, (0, 0, 255), -1)
        cv2.putText(frame, "REC", (34, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
    if not connected:
        cv2.putText(frame, "RECONNECTING...", (10, 55), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 165, 255), 2, cv2.LINE_AA)
    return frame


def save_snapshot(frame, out_dir, prefix):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.jpg")
    cv2.imwrite(path, frame)
    log.info("Snapshot saved: %s", path)


def parse_args():
    p = argparse.ArgumentParser(description="View and record an IP camera feed.")
    p.add_argument("source", help="Camera URL (rtsp://, http://), video file, or webcam index (0)")
    p.add_argument("-o", "--output", default="recordings", help="Output folder (default: recordings)")
    p.add_argument("--record", action="store_true", help="Start recording immediately")
    p.add_argument("--duration", type=float, default=0, help="Stop after N seconds (0 = run until quit)")
    p.add_argument("--segment", type=float, default=0,
                   help="Split recordings into files of N seconds each (0 = one file)")
    p.add_argument("--fps", type=float, default=0, help="Recording FPS (default: camera FPS, or 20)")
    p.add_argument("--format", default="mp4", choices=["mp4", "avi", "mkv"], help="Video container")
    p.add_argument("--width", type=int, default=0, help="Resize frames to this width (keeps aspect)")
    p.add_argument("--prefix", default="cam", help="File name prefix")
    p.add_argument("--no-timestamp", action="store_true", help="Do not burn a timestamp into the video")
    p.add_argument("--no-display", action="store_true", help="Headless mode (no window)")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    stream = CameraStream(args.source).start()

    log.info("Waiting for first frame...")
    deadline = time.time() + 30
    while stream.read()[1] is None:
        if time.time() > deadline or stream.finished:
            log.error("No video received from the camera. Check the URL, credentials and network.")
            stream.stop()
            return 1
        time.sleep(0.1)

    fps = args.fps or stream.fps or 20.0
    recorder = Recorder(args.output, fps, "." + args.format, args.segment, args.prefix)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    window = "IP Camera  [r] record  [s] snapshot  [q] quit"
    if not args.no_display:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    start_time = time.time()
    frame_interval = 1.0 / fps
    next_write = time.time()
    last_frame = None
    last_id = -1

    try:
        while not stop.is_set():
            if args.duration and time.time() - start_time >= args.duration:
                log.info("Duration reached")
                break
            if stream.finished and last_id == stream.frame_id:
                break

            frame_id, frame = stream.read()
            if frame is not None and frame_id != last_id:
                if args.width and frame.shape[1] != args.width:
                    h = int(frame.shape[0] * args.width / frame.shape[1])
                    frame = cv2.resize(frame, (args.width, h))
                last_frame = draw_timestamp(frame, not args.no_timestamp)
                last_id = frame_id

            if last_frame is None:
                time.sleep(0.01)
                continue

            if args.record and not recorder.active:
                recorder.start((last_frame.shape[1], last_frame.shape[0]))
                args.record = False

            # Write at a steady FPS so playback speed matches real time,
            # duplicating the last frame if the camera stalls briefly.
            now = time.time()
            if recorder.active and now >= next_write:
                recorder.write(last_frame)
                next_write += frame_interval
                if now - next_write > 1.0:  # fell far behind, resync
                    next_write = now + frame_interval

            if args.no_display:
                time.sleep(max(0.0, min(0.01, next_write - time.time())))
                continue

            cv2.imshow(window, draw_status(last_frame.copy(), recorder.active, stream.connected))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                recorder.toggle((last_frame.shape[1], last_frame.shape[0]))
                next_write = time.time()
            elif key == ord("s"):
                save_snapshot(last_frame, args.output, args.prefix)
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        recorder.stop()
        stream.stop()
        if not args.no_display:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
