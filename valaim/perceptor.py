"""Async perception pipeline: capture+inference run in a dedicated thread at
max hardware rate; the aim loop consumes only FRESH results (each snapshot is
delivered once), so stroke timing never waits behind a bus.

The worker owns its own ScreenCapture (mss instances are per-thread). If the
thread cannot start (headless/dev environments), the caller falls back to the
serial path transparently.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class Snapshot:
    seq: int
    img: object
    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    cursor: tuple[int, int]
    detections: list
    ts: float


class Perceptor:
    def __init__(self, capture_factory, detector):
        self._capture_factory = capture_factory
        self._detector = detector
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._latest: Snapshot | None = None
        self._seq = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="perceptor", daemon=True)
        self._errors = 0

    def start(self) -> bool:
        try:
            self._thread.start()
        except (RuntimeError, OSError):
            return False
        for _ in range(80):
            if self._latest is not None or self._errors > 3:
                break
            time.sleep(0.05)
        return self._latest is not None

    def _run(self) -> None:
        try:
            cap = self._capture_factory()
        except Exception:
            with self._lock:
                self._errors = 99
            return
        while not self._stop.is_set():
            try:
                img, cx, cy, cw, ch = cap.grab()
                dets = self._detector.detect(img, cx, cy)
                cursor = cap.crosshair()
            except Exception:
                with self._lock:
                    self._errors += 1
                    if self._errors > 50:
                        return
                time.sleep(0.05)
                continue
            with self._cond:
                self._seq += 1
                self._latest = Snapshot(self._seq, img, cx, cy, cw, ch, cursor, dets, time.monotonic())
                self._cond.notify_all()

    def pop_new(self, timeout: float) -> Snapshot | None:
        """Block until a NEWER snapshot exists, and consume it."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            cur = self._latest
            while cur is None or cur.seq <= self._served:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
                cur = self._latest
            self._served = cur.seq
            return cur

    def stop(self) -> None:
        self._stop.set()

    _served = 0
