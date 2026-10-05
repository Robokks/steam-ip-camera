"""
Tiny line-based TCP command server (one thread per client).

Protocol — plain ASCII text, one command per line, ';' separates fields,
every reply is ONE line ending in CR LF (LabVIEW: TCP Read in CRLF mode):

    PING                               -> OK PONG
    START                              -> OK START;<file path>
    START;<folder>                     -> OK START;<file path>
    START;<folder>;<file name>         -> OK START;<file path>
    STOP                               -> OK STOP;<saved file path>
    STATUS                             -> OK <RECORDING;path | WAITING_MOTION | IDLE | CONNECTING | DISCONNECTED>
    SNAPSHOT[;<folder>[;<file name>]]  -> OK SNAPSHOT;<jpg path>
    CONNECT / DISCONNECT               -> OK CONNECT / OK DISCONNECT
    anything else                      -> ERROR <reason>

Empty fields use the app's current setting, e.g. "START;;test_01".
A client may send many commands on one connection, or connect per command.
"""

import socket
import threading


class CommandServer(threading.Thread):
    def __init__(self, handler, port=5000, host="0.0.0.0", log=print):
        """handler(command_line) -> reply string (without CR LF)."""
        super().__init__(daemon=True)
        self.handler = handler
        self.host = host
        self.port = int(port)
        self.log = log
        self.clients = 0
        self._sock = None
        self._stopped = threading.Event()
        self.error = ""

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.host, self.port))
            self._sock.listen(5)
            self._sock.settimeout(0.5)
        except OSError as e:
            self.error = f"Cannot listen on port {self.port}: {e}"
            self.log(self.error)
            return
        self.log(f"TCP server listening on port {self.port}")
        while not self._stopped.is_set():
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn, addr), daemon=True).start()
        try:
            self._sock.close()
        except OSError:
            pass

    def stop(self):
        """Stop listening and free the port right away (so it can be reused)."""
        self._stopped.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=2)

    def _serve(self, conn, addr):
        peer = f"{addr[0]}:{addr[1]}"
        self.clients += 1
        self.log(f"Client connected {peer}")
        buf = b""
        conn.settimeout(0.5)
        try:
            while not self._stopped.is_set():
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                # Accept LF, CR LF or a lone CR as end of command.
                buf = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    self.log(f"{peer} > {text}")
                    try:
                        reply = self.handler(text)
                    except Exception as e:  # noqa: BLE001 — never kill the connection
                        reply = f"ERROR {e}"
                    self.log(f"{peer} < {reply}")
                    conn.sendall((reply + "\r\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            conn.close()
            self.clients -= 1
            self.log(f"Client disconnected {peer}")


def parse_command(line):
    """'START;C:\\Rec;test' -> ('START', ['C:\\Rec', 'test'])"""
    parts = [p.strip() for p in line.split(";")]
    return parts[0].upper(), parts[1:]
