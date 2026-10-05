"""
Find the RTSP URL of an IP camera.

1. Asks the camera itself over ONVIF (most reliable).
2. Falls back to trying common RTSP paths and reports which ones give video.

Usage:
    python find_rtsp_url.py <ip> <user> <password> [rtsp_port] [http_port]
"""

import base64
import hashlib
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

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


def _soap(url, body, user, password):
    """POST an ONVIF SOAP request with WS-Security digest auth; return XML root."""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    envelope = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
<UsernameToken><Username>{user}</Username>
<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>
<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
</UsernameToken></Security></s:Header>
<s:Body xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
 xmlns:tt="http://www.onvif.org/ver10/schema">{body}</s:Body></s:Envelope>"""
    req = urllib.request.Request(url, envelope.encode(), {"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return ET.fromstring(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url} (wrong password, or ONVIF disabled?)") from None


def _find_all(root, tag):
    return [el for el in root.iter() if el.tag.split("}")[-1] == tag]


def onvif_urls(ip, user, password, http_port):
    """Ask the camera for its stream URLs using ONVIF (no extra packages needed)."""
    device_url = f"http://{ip}:{http_port}/onvif/device_service"
    print(f"Asking the camera for its stream URLs via ONVIF ({device_url}) ...")
    try:
        caps = _soap(device_url, "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>",
                     user, password)
        xaddrs = [el.text for el in _find_all(caps, "XAddr") if el.text and "media" in el.text.lower()]
        media_url = xaddrs[0] if xaddrs else device_url
        profiles = _soap(media_url, "<trt:GetProfiles/>", user, password)
        urls = []
        for prof in _find_all(profiles, "Profiles"):
            token = prof.get("token")
            names = _find_all(prof, "Name")
            name = names[0].text if names else token
            resp = _soap(media_url, f"""<trt:GetStreamUri><trt:StreamSetup>
<tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
</trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken></trt:GetStreamUri>""", user, password)
            uri = _find_all(resp, "Uri")
            if uri and uri[0].text:
                print(f"  {name}: {uri[0].text}")
                urls.append(with_credentials(uri[0].text.strip(), user, password))
        if not urls:
            print("  Camera answered but returned no stream URLs.")
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
