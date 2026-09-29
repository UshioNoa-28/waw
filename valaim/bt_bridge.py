"""PC-side client for the BtAimBridge Android app.

Architecture:

    ValAim (this process)  --TCP-->  phone app  --Bluetooth HID-->  game

The phone has paired with Windows as a Bluetooth HID mouse (verified to work
while Vanguard-protected games are focused). This client sends relative mouse
commands to the phone over the LAN so the aim tool can drive the cursor without
ever calling SendInput.

Protocol (ASCII, one command per line):

    M <dx> <dy>\\n     relative move in mouse counts
    B <mask>\\n        button mask: bit0 left, bit1 right, bit2 middle
    W <ticks>\\n       wheel
    P\\n               keepalive

The socket is kept open on a background thread with a small send queue, so a
stall on the phone side does not block the aim loop.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections import deque

DEFAULT_PORT = 47800

BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04


class BtBridgeError(RuntimeError):
    pass


class BtBridge:
    """Threaded TCP client to the phone. Auto-reconnects when the link drops."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        connect_timeout: float = 3.0,
        send_hz_cap: int = 1000,
    ) -> None:
        if sys.platform != "win32" and sys.platform != "linux":
            raise BtBridgeError("TCP bridge runs on any OS")
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self._send_interval = 1.0 / max(1, send_hz_cap)

        self._sock: socket.socket | None = None
        self._queue: deque[bytes] = deque(maxlen=1024)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._running = True
        self._connected = False
        self._last_error = ""

        self._thread = threading.Thread(target=self._run, name="bt-bridge", daemon=True)
        self._thread.start()

    # -- connection ----------------------------------------------------

    def _connect(self) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), self.connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(None)
            with self._lock:
                self._sock = sock
                self._connected = True
            return True
        except OSError as exc:
            self._last_error = str(exc)
            return False

    def _run(self) -> None:
        while self._running:
            if not self._connected:
                if not self._connect():
                    time.sleep(1.0)
                    continue
            try:
                self._pump()
            except OSError as exc:
                self._last_error = str(exc)
                self._drop()
            time.sleep(0)

    def _pump(self) -> None:
        while self._running:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(timeout=0.5)
                batch = b"".join(self._queue)
                self._queue.clear()
                sock = self._sock
            if sock is None:
                return
            if not batch:
                continue
            sock.sendall(batch)

    def _drop(self) -> None:
        with self._lock:
            sock = self._sock
            self._sock = None
            self._connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # -- outgoing commands ---------------------------------------------

    def _enqueue(self, line: str) -> None:
        data = (line + "\n").encode("ascii")
        with self._cond:
            self._queue.append(data)
            self._cond.notify()

    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        self._enqueue(f"M {int(dx)} {int(dy)}")

    def set_buttons(self, mask: int) -> None:
        self._enqueue(f"B {int(mask) & 0x1F}")

    def wheel(self, ticks: int) -> None:
        if ticks:
            self._enqueue(f"W {int(ticks)}")

    def ping(self) -> None:
        self._enqueue("P")

    # -- status --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def close(self) -> None:
        self._running = False
        with self._cond:
            self._cond.notify_all()
        self._drop()
        self._thread.join(timeout=1.0)


def is_available() -> bool:
    return True
