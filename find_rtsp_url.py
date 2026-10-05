"""
Find the RTSP URL of an IP camera.

1. Asks the camera itself over ONVIF (most reliable, needs `pip install onvif-zeep`).
2. Falls back to trying common RTSP paths and reports which ones give video.

Usage:
    python find_rtsp_url.py <ip> <user> <password> [rtsp_port] [http_port]
"""

import os
import sys

import cv2

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"

PATHS = [
    "/stream1", "/stream2",                     # many ONVIF cams
    "/unicaststream/1", "/unicaststream/2",     # Matrix SATATYA (some firmware)
    "/live/stream1", "/live/stream2",
    "/media/video1", "/media/video2",
    "/11", "/12", "/onvif1", "/ch0_0.h264",
    "/0/av0", "/0/av1",
    "/Streaming/Channels/101", "/Streaming/Channels/102",   # Hikvision-style
    "/cam/realmonitor?channel=1&subtype=0",     # Dahua-style
    "/h264Preview_01_main",                     # Reolink-style
    "/live/main", "/live", "/h264", "/profile1", "/",
]


def with_credentials(url, user, password):
    """Insert user:password into an rtsp:// URL returned by ONVIF."""
    scheme, rest = url.split("://", 1)
    if "@" in rest.split("/", 1)[0]:
        return url
    return f"{scheme}://{user}:{password}@{rest}"


def onvif_urls(ip, user, password, http_port):
    try:
        from onvif import ONVIFCamera
    except ImportError:
        print("ONVIF check skipped (run `pip install onvif-zeep` to enable it).\n")
        return []
    print(f"Asking the camera for its stream URLs via ONVIF (port {http_port}) ...")
    try:
        cam = ONVIFCamera(ip, int(http_port), user, password)
        media = cam.create_media_service()
        urls = []
        for profile in media.GetProfiles():
            req = media.create_type("GetStreamUri")
            req.ProfileToken = profile.token
            req.StreamSetup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}
            uri = media.GetStreamUri(req).Uri
            print(f"  {profile.Name}: {uri}")
            urls.append(with_credentials(uri, user, password))
        print()
        return urls
    except Exception as e:  # noqa: BLE001 — any ONVIF failure just means fall back
        print(f"  ONVIF failed: {e}")
        print("  (Check that ONVIF is enabled in the camera's web page.)\n")
        return []


def test_url(url, password):
    shown = url.replace(f":{password}@", ":***@")
    print(f"Trying {shown} ...", end=" ", flush=True)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    ok, frame = cap.read() if cap.isOpened() else (False, None)
    cap.release()
    if ok:
        h, w = frame.shape[:2]
        print(f"OK  {w}x{h}")
        return w, h
    print("no")
    return None


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    ip, user, password = sys.argv[1:4]
    port = sys.argv[4] if len(sys.argv) > 4 else "554"
    http_port = sys.argv[5] if len(sys.argv) > 5 else "80"

    candidates = onvif_urls(ip, user, password, http_port)
    candidates += [f"rtsp://{user}:{password}@{ip}:{port}{p}" for p in PATHS]

    found = []
    seen = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        size = test_url(url, password)
        if size:
            found.append((url, *size))

    print()
    if not found:
        print("No working path found. Check that the IP/password are right, RTSP is")
        print("enabled in the camera's web page, and the RTSP port is correct.")
        return 1
    print("Working URLs (use the highest resolution one for recording):")
    for url, w, h in found:
        print(f"  {w}x{h}  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
