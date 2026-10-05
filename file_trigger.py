"""
File-based trigger: watch a command file, run what's in it, write a reply file.

Write the command file (default C:\\CameraTrigger\\command.txt) in either format:

  One line, same as TCP:            Or key = value lines:
      START                             CMD=START
      START;C:\\Recordings;Test_001      FOLDER=C:\\Recordings
      STOP                              FILE=Test_001

Several one-line commands may be put on separate lines; they run in order.

After the commands have run, the app:
  1. writes the reply next to it as  command_reply.txt   (e.g. "OK START;C:\\Recordings\\Test_001.mp4")
  2. deletes command.txt — so "file gone" means "command was taken".
"""

import os
import threading
import time
from datetime import datetime

KEY_ALIASES = {
    "CMD": "cmd", "COMMAND": "cmd", "ACTION": "cmd",
    "FOLDER": "folder", "PATH": "folder", "DIR": "folder", "DIRECTORY": "folder",
    "FILE": "name", "FILENAME": "name", "FILE_NAME": "name", "NAME": "name",
}


def parse_command_file(text):
    """Return a list of one-line commands ('START;folder;name') from file text."""
    lines = [ln.strip() for ln in text.replace("\ufeff", "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    fields = {}
    plain = []
    for ln in lines:
        key, sep, value = ln.partition("=")
        alias = KEY_ALIASES.get(key.strip().upper()) if sep else None
        if alias:
            fields[alias] = value.strip().strip('"')
        else:
            plain.append(ln)
    if fields:
        cmd = fields.get("cmd", "").upper()
        if not cmd:
            return ["ERROR_NO_CMD"]
        if cmd in ("START", "SNAPSHOT"):
            return [f"{cmd};{fields.get('folder', '')};{fields.get('name', '')}"]
        return [cmd]
    return plain


def reply_path_for(command_path):
    stem, ext = os.path.splitext(command_path)
    return f"{stem}_reply{ext or '.txt'}"


class FileTrigger(threading.Thread):
    POLL_SEC = 0.2

    def __init__(self, path, handler, log=print):
        """handler(command_line) -> reply string."""
        super().__init__(daemon=True)
        self.path = path
        self.handler = handler
        self.log = log
        self.error = ""
        self.last_reply = ""
        self._stopped = threading.Event()

    def stop(self):
        self._stopped.set()

    def run(self):
        folder = os.path.dirname(self.path) or "."
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.error = f"Cannot create {folder}: {e}"
            self.log(self.error)
            return
        self.log(f"Watching trigger file {self.path}")
        last_sig = None
        done_sig = None                  # file we processed but could not delete
        while not self._stopped.wait(self.POLL_SEC):
            try:
                st = os.stat(self.path)
            except FileNotFoundError:
                last_sig = None
                continue
            except OSError:
                continue
            # Only act once size+mtime stayed the same for one poll (file fully written).
            sig = (st.st_size, st.st_mtime)
            if sig == done_sig:
                continue
            if sig != last_sig:
                last_sig = sig
                continue
            last_sig = None
            if not self._process():
                done_sig = sig

    def _process(self):
        try:
            with open(self.path, encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError:
            return True                  # still locked by the writer; try next poll
        commands = parse_command_file(text)
        replies = []
        for cmd in commands:
            if cmd == "ERROR_NO_CMD":
                reply = "ERROR CMD= missing"
            else:
                self.log(f"file > {cmd}")
                try:
                    reply = self.handler(cmd)
                except Exception as e:  # noqa: BLE001
                    reply = f"ERROR {e}"
            self.log(f"file < {reply}")
            replies.append(reply)
        if not replies:
            replies = ["ERROR EMPTY FILE"]
        self.last_reply = replies[-1]
        try:
            with open(reply_path_for(self.path), "w", encoding="utf-8") as f:
                f.write("\n".join(replies) + "\n")
                f.write(f"# {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        except OSError as e:
            self.log(f"Cannot write reply file: {e}")
        for _ in range(10):              # delete = "command taken"
            try:
                os.remove(self.path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                time.sleep(0.1)
        self.log(f"Cannot delete {self.path} — it will not be run again until it changes")
        return False
