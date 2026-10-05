"""
Matrix IP Camera — Video Recorder
Modes: continuous | timed | motion (24x7 with auto file split)

Camera settings can be overridden with environment variables so the
password does not have to live in the source file:
    CAM_IP, CAM_USER, CAM_PASS, CAM_URL (full URL, overrides the others)
"""

import cv2
import os
import time
from collections import deque
from datetime import datetime

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────
CAM_IP    = os.getenv("CAM_IP", "192.168.1.126")
USERNAME  = os.getenv("CAM_USER", "admin")
PASSWORD  = os.getenv("CAM_PASS", "admin123")
# Matrix SATATYA: /unicaststream/1 = main (1080p), /unicaststream/2 = sub stream
RTSP_URL  = os.getenv("CAM_URL", f"rtsp://{USERNAME}:{PASSWORD}@{CAM_IP}:554/unicaststream/1")

SAVE_DIR       = "recordings"          # folder to save .mp4 files
SPLIT_MINUTES  = 10                    # split a new file every N minutes
DEFAULT_FPS    = 25.0                  # used only if the camera doesn't report FPS
RECONNECT_SEC  = 5                     # wait between reconnect attempts


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
os.makedirs(SAVE_DIR, exist_ok=True)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"


def safe_url(url: str) -> str:
    """Hide the password when printing the URL."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"
    return url


def new_writer(resolution: tuple, fps: float, prefix: str = "cam") -> tuple[cv2.VideoWriter, str]:
    """Create a new VideoWriter with a timestamped filename."""
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SAVE_DIR, f"{prefix}_{ts}.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, resolution)
    if not writer.isOpened():
        raise IOError(f"Cannot create video file: {path}")
    print(f"[REC] New file → {path}")
    return writer, path


def open_stream(url: str):
    """Open the stream and return (cap, first_frame, (w, h), fps).

    The size is taken from the first real frame: if it doesn't match the
    VideoWriter size exactly, OpenCV silently drops every frame and you
    end up with empty files.
    """
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        raise ConnectionError(f"Cannot open stream: {safe_url(url)}")
    ret, frame = cap.read()
    if not ret:
        cap.release()
        raise ConnectionError(f"Stream opened but no frames: {safe_url(url)}")
    h, w = frame.shape[:2]
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if 1 <= fps <= 120 else DEFAULT_FPS
    print(f"[STREAM] Connected  {w}x{h} @ {fps:.1f} fps")
    return cap, frame, (w, h), fps


def close_preview(show_preview: bool):
    if show_preview:
        cv2.destroyAllWindows()


# ─────────────────────────────────────────────
#  1. Continuous recording with auto file split
# ─────────────────────────────────────────────
def record_continuous(rtsp_url: str = RTSP_URL,
                      split_minutes: int = SPLIT_MINUTES,
                      show_preview: bool = False):
    """
    Records indefinitely, splitting into a new file every `split_minutes`.
    Reconnects automatically if the camera drops. Press Ctrl+C to stop.
    """
    print(f"[REC] Continuous recording — split every {split_minutes} min")
    print("[REC] Press Ctrl+C to stop.\n")
    split_secs = split_minutes * 60

    try:
        while True:
            cap = writer = None
            path = None
            frames_written = 0
            try:
                cap, frame, res, fps = open_stream(rtsp_url)
                writer, path = new_writer(res, fps)
                t_start = time.time()

                while True:
                    writer.write(frame)
                    frames_written += 1

                    if show_preview:
                        cv2.imshow("Recording (q=quit)", frame)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            raise KeyboardInterrupt

                    # Split file at interval
                    if time.time() - t_start >= split_secs:
                        writer.release()
                        print(f"[REC] Closed: {path}  ({frames_written} frames)")
                        writer, path = new_writer(res, fps)
                        t_start = time.time()
                        frames_written = 0

                    ret, frame = cap.read()
                    if not ret:
                        print("[WARN] Frame lost — reconnecting...")
                        break

            except (ConnectionError, IOError) as e:
                print(f"[ERROR] {e} — retrying in {RECONNECT_SEC} s...")
            finally:
                if writer is not None:
                    writer.release()
                    print(f"[REC] Closed: {path}  ({frames_written} frames)")
                if cap is not None:
                    cap.release()
            time.sleep(RECONNECT_SEC)

    except KeyboardInterrupt:
        print("\n[REC] Stopped by user.")
    finally:
        close_preview(show_preview)


# ─────────────────────────────────────────────
#  2. Timed recording — record for N seconds
# ─────────────────────────────────────────────
def record_timed(duration_sec: int = 60,
                 rtsp_url: str = RTSP_URL,
                 show_preview: bool = False):
    """Record for `duration_sec` seconds then stop."""
    print(f"[REC] Timed recording: {duration_sec} s")

    cap, frame, res, fps = open_stream(rtsp_url)
    writer, path = new_writer(res, fps)
    t_start = time.time()
    frames = 0

    try:
        while time.time() - t_start < duration_sec:
            writer.write(frame)
            frames += 1

            elapsed = time.time() - t_start
            print(f"\r[REC] {elapsed:.1f}/{duration_sec} s  |  {frames} frames", end="")

            if show_preview:
                cv2.imshow("Recording (q=quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            ret, frame = cap.read()
            if not ret:
                print("\n[WARN] Frame lost.")
                break
    except KeyboardInterrupt:
        print("\n[REC] Stopped by user.")
    finally:
        print()
        writer.release()
        cap.release()
        close_preview(show_preview)
        print(f"[DONE] Saved: {path}  ({frames} frames)")


# ─────────────────────────────────────────────
#  3. Motion-triggered recording
# ─────────────────────────────────────────────
def record_motion(rtsp_url: str = RTSP_URL,
                  min_area: int = 5000,
                  post_motion_sec: int = 10,
                  pre_motion_sec: int = 3,
                  show_preview: bool = True):
    """
    Start recording when motion is detected, stop after
    `post_motion_sec` of no motion. The `pre_motion_sec` seconds before
    the motion are also saved, so the start of the event isn't lost.
    `min_area` is in pixels of the full-size frame.
    """
    print("[MOTION] Watching for motion. Ctrl+C to stop.")
    DETECT_WIDTH = 640          # detect on a small copy — 1080p is too slow
    WARMUP_FRAMES = 50          # let the background model settle first

    writer = cap = None
    path = None
    try:
        while True:
            try:
                cap, frame, res, fps = open_stream(rtsp_url)
            except ConnectionError as e:
                print(f"[ERROR] {e} — retrying in {RECONNECT_SEC} s...")
                time.sleep(RECONNECT_SEC)
                continue

            scale = DETECT_WIDTH / res[0] if res[0] > DETECT_WIDTH else 1.0
            small_size = (int(res[0] * scale), int(res[1] * scale))
            small_min_area = min_area * scale * scale
            bg_sub = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25,
                                                        detectShadows=True)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            pre_buffer = deque(maxlen=int(pre_motion_sec * fps))
            last_motion = 0.0
            frame_no = 0

            while True:
                frame_no += 1

                # Motion detection on a downscaled, blurred copy
                small = cv2.resize(frame, small_size) if scale < 1.0 else frame
                small = cv2.GaussianBlur(small, (5, 5), 0)
                mask = bg_sub.apply(small)
                # MOG2 marks shadows as 127 — keep only real foreground (255)
                _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                motion = frame_no > WARMUP_FRAMES and any(
                    cv2.contourArea(c) > small_min_area for c in contours)

                if motion:
                    last_motion = time.time()
                    if writer is None:
                        writer, path = new_writer(res, fps, prefix="motion")
                        print("[MOTION] Detected — recording started.")
                        while pre_buffer:
                            writer.write(pre_buffer.popleft())

                if writer is not None:
                    writer.write(frame)
                    # Stop if no motion for post_motion_sec
                    if time.time() - last_motion > post_motion_sec:
                        writer.release()
                        print(f"[MOTION] No motion — saved: {path}")
                        writer, path = None, None
                else:
                    pre_buffer.append(frame)

                if show_preview:
                    view = frame.copy()     # don't draw on the recorded frame
                    label = "REC" if writer is not None else "watching"
                    color = (0, 0, 255) if writer is not None else (0, 255, 0)
                    cv2.putText(view, label, (20, 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)
                    cv2.imshow("Motion Recorder (q=quit)", view)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        raise KeyboardInterrupt

                ret, frame = cap.read()
                if not ret:
                    print("[WARN] Frame lost — reconnecting...")
                    break

            cap.release()
            cap = None
            if writer is not None:
                writer.release()
                print(f"[MOTION] Saved: {path}")
                writer, path = None, None
            time.sleep(RECONNECT_SEC)

    except KeyboardInterrupt:
        print("\n[MOTION] Stopped.")
    finally:
        if writer is not None:
            writer.release()
            print(f"[MOTION] Saved: {path}")
        if cap is not None:
            cap.release()
        close_preview(show_preview)


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "help"

    if mode == "continuous":
        # python matrix_record.py continuous [split_minutes] [preview]
        split   = float(sys.argv[2]) if len(sys.argv) > 2 else SPLIT_MINUTES
        preview = sys.argv[3].lower() == "true" if len(sys.argv) > 3 else False
        record_continuous(split_minutes=split, show_preview=preview)

    elif mode == "timed":
        # python matrix_record.py timed <seconds> [preview]
        dur     = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        preview = sys.argv[3].lower() == "true" if len(sys.argv) > 3 else False
        record_timed(duration_sec=dur, show_preview=preview)

    elif mode == "motion":
        # python matrix_record.py motion [preview]
        preview = sys.argv[2].lower() == "true" if len(sys.argv) > 2 else True
        record_motion(show_preview=preview)

    else:
        print("""
Matrix IP Camera — Recorder
Usage:
  python matrix_record.py continuous [split_min] [preview:true/false]
      Records 24x7, splits a new file every N minutes (default 10)

  python matrix_record.py timed <seconds> [preview:true/false]
      Records for exactly N seconds then exits

  python matrix_record.py motion [preview:true/false]
      Records only when motion is detected (with 3 s pre-roll)

Camera login via environment variables (recommended):
  CAM_IP=192.168.1.100 CAM_USER=admin CAM_PASS=secret python matrix_record.py continuous

Examples:
  python matrix_record.py continuous 30 true    # 30-min files, with preview
  python matrix_record.py timed 300             # record 5 minutes
  python matrix_record.py motion                # motion-triggered
""")
