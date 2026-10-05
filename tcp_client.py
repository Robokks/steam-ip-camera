"""
Send commands to the IP Camera Recorder app over TCP (for testing / scripting).

    python tcp_client.py START "C:\\Recordings" test_001
    python tcp_client.py STOP
    python tcp_client.py STATUS
    python tcp_client.py            (interactive: type commands, empty line quits)

Options: --host 127.0.0.1 --port 5000
"""

import argparse
import socket


def send(sock, line):
    sock.sendall((line + "\r\n").encode())
    reply = b""
    while not reply.endswith(b"\r\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        reply += chunk
    return reply.decode().strip()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="*", help="e.g. START C:\\Rec name  |  STOP  |  STATUS")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    a = p.parse_args()

    with socket.create_connection((a.host, a.port), timeout=20) as s:
        if a.command:
            print(send(s, ";".join([a.command[0].upper()] + a.command[1:])))
            return
        print(f"Connected to {a.host}:{a.port}. Type commands (empty line to quit).")
        while True:
            line = input("> ").strip()
            if not line:
                break
            print(send(s, line))


if __name__ == "__main__":
    main()
