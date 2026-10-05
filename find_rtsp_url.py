"""
Try common RTSP paths against a camera and report which ones give video.

Usage:
    python find_rtsp_url.py <ip> <user> <password> [port]
"""

import os
import sys

import cv2

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"

PATHS = [
    "/stream1", "/stream2",                     # Matrix SATATYA, Tapo, many ONVIF cams
    "/media/video1", "/media/video2",           # Matrix (some models)
    "/0/av0", "/0/av1",
    "/Streaming/Channels/101", "/Streaming/Channels/102",   # Hikvision-style
    "/cam/realmonitor?channel=1&subtype=0",     # Dahua-style
    "/h264Preview_01_main",                     # Reolink-style
    "/live/main", "/live", "/h264", "/ch0_0.h264", "/onvif1", "/11", "/",
]


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    ip, user, password = sys.argv[1:4]
    port = sys.argv[4] if len(sys.argv) > 4 else "554"

    found = []
    for path in PATHS:
        url = f"rtsp://{user}:{password}@{ip}:{port}{path}"
        shown = url.replace(f":{password}@", ":***@")
        print(f"Trying {shown} ...", end=" ", flush=True)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        if ok:
            h, w = frame.shape[:2]
            print(f"OK  {w}x{h}")
            found.append((url, w, h))
        else:
            print("no")

    print()
    if not found:
        print("No working path found. Check that the IP/password are right, RTSP is")
        print("enabled in the camera's web page, and port 554 is correct.")
        return 1
    print("Working URLs (use the highest resolution one for recording):")
    for url, w, h in found:
        print(f"  {w}x{h}  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
