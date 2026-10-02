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

Coalescing (important for stability): Bluetooth can stall briefly and pile up
backlog. If we replayed every queued frame, 15 stale corrections would fire at
once and the crosshair would fly past the target. So only the LATEST pending
move is sent per drain (each new frame supersedes the previous one), while
wheel ticks accumulate and button state is edge-triggered.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time

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
    ) -> None:
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._running = True
        self._connected = False
        self._last_error = ""

        # latest-wins pending move; accumulating wheel; edge-triggered buttons
        self._pend_dx = 0
        self._pend_dy = 0
        self._pend_wheel = 0
        self._buttons = 0
        self._sent_buttons = 0
        self._dirty = False
        self._flips = 0
        self._nail = -1   # -1 unknown, 0/1 last sent state

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
                # fresh session: make sure buttons match on the next flush
                self._sent_buttons = -1
                self._dirty = True
            threading.Thread(target=self._read_loop, args=(sock,),
                             name="bt-bridge-rx", daemon=True).start()
            return True
        except OSError as exc:
            self._last_error = str(exc)
            return False

    def _run(self) -> None:
        was = False
        fails = 0
        while self._running:
            if not self._connected:
                if not self._connect():
                    fails += 1
                    # Never silently talk to an empty address (typo'd IP is a
                    # classic - shout after ~5s, then every 30s).
                    if fails == 5 or fails % 30 == 0:
                        print(f"[bt] cannot reach {self.host}:{self.port} "
                              f"({self._last_error}) - check the phone IP and that the app shows START",
                              file=sys.stderr)
                    time.sleep(1.0)
                    continue
            fails = 0
            if not was:
                was = True
                print(f"[bt] link up -> {self.host}:{self.port}")
            try:
                self._pump()
            except OSError as exc:
                self._last_error = str(exc)
                self._drop()
                if was:
                    was = False
                    print(f"[bt] link dropped: {exc}; retrying")
            time.sleep(0)

    def _read_loop(self, sock: socket.socket) -> None:
        """Inbound lines from the phone (L = lock toggle press)."""
        buf = b""
        try:
            while self._running and self._sock is sock:
                data = sock.recv(256)
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if line.strip() == b"L":
                        with self._lock:
                            self._flips += 1
                    elif line.startswith(b"C "):
                        try:
                            import base64 as _b64
                            text = _b64.b64decode(line[2:].strip()).decode("utf-8", "replace")
                            with open(os.path.join(os.getcwd(), "android_crash.log"), "a", encoding="utf-8") as _f:
                                _f.write(f"\n===== pulled {time.strftime('%H:%M:%S')} =====\n{text}\n")
                        except Exception:
                            pass
        except OSError:
            pass

    def set_nail(self, on: bool) -> None:
        v = 1 if on else 0
        with self._cond:
            if v == self._nail:
                return
            self._nail = v
            self._dirty = True
            self._cond.notify_all()

    def pop_lock_flips(self) -> int:
        with self._lock:
            n, self._flips = self._flips, 0
        return n

    def _pump(self) -> None:
        last_tx = time.monotonic()
        while self._running:
            with self._cond:
                while self._running and not self._dirty and time.monotonic() - last_tx <= 3.0:
                    self._cond.wait(timeout=0.4)
                if not self._running:
                    return
                sock = self._sock
                lines: list[str] = []
                if self._dirty:
                    if self._nail >= 0:
                        lines.append(f"N {self._nail}")
                    if self._buttons != self._sent_buttons:
                        lines.append(f"B {self._buttons & 0x1F}")
                        self._sent_buttons = self._buttons
                    dx, dy = self._pend_dx, self._pend_dy
                    wheel = self._pend_wheel
                    self._pend_dx = self._pend_dy = 0
                    self._pend_wheel = 0
                    self._dirty = False
                    if dx or dy:
                        lines.append(f"M {dx} {dy}")
                    if wheel:
                        lines.append(f"W {1 if wheel > 0 else -1}")
                if not lines:
                    lines.append("P")
            if sock is None:
                return
            payload = ("\n".join(lines) + "\n").encode("ascii")
            sock.sendall(payload)
            last_tx = time.monotonic()

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

    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        with self._cond:
            self._pend_dx = int(dx)
            self._pend_dy = int(dy)
            self._dirty = True
            self._cond.notify()

    def set_buttons(self, mask: int) -> None:
        with self._cond:
            self._buttons = int(mask) & 0x1F
            self._dirty = True
            self._cond.notify()

    def wheel(self, ticks: int) -> None:
        if not ticks:
            return
        with self._cond:
            self._pend_wheel += int(ticks)
            self._dirty = True
            self._cond.notify()

    def ping(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify()

    # -- status --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def close(self) -> None:
        # Make sure the host sees "no buttons held" before the socket dies,
        # otherwise a stuck virtual left click survives this session.
        try:
            with self._cond:
                sock = self._sock
            if sock is not None:
                sock.sendall(b"B 0\nP\n")
                time.sleep(0.2)
        except OSError:
            pass
        self._running = False
        with self._cond:
            self._cond.notify_all()
        self._drop()
        self._thread.join(timeout=1.0)


def is_available() -> bool:
    return True
