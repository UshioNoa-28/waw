"""Aim shaping: turn a pixel error into a mouse step.

The detector gives a target position every frame; the naive response is
``move(error * fraction)``, which produces a dead-straight, perfectly
proportional snap. This module adds three cheap layers that break that
signature:

  * **proportional damping** - move a fraction of the remaining error, so the
    approach is exponential rather than a teleport;
  * **overshoot** - on a large error, briefly move past the target before
    settling, mirroring how a hand corrects;
  * **micro-tremor** - a small stochastic jitter on every step, the action
    tremor a human hand cannot suppress.

Fully human behavioural shaping (reaction delay, Ornstein-Uhlenbeck tremor
band, idle drift) is intentionally left as a later stage; the hooks are here.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class AimParams:
    move_fraction: float = 0.65
    max_step: int = 120
    min_move: float = 2.0

    deadzone: float = 1.5
    tremor_px: float = 0.6
    tremor_freq: float = 8.0

    overshoot_factor: float = 1.06
    overshoot_error: float = 120.0
    overshoot_frames: int = 2

    max_segment: int = 127


class AimEngine:
    """Stateful shaper. One instance per session."""

    def __init__(self, params: AimParams | None = None, seed: int | None = None) -> None:
        self.p = params or AimParams()
        self._rng = random.Random(seed)
        self._overshoot_left = 0
        self._phase = 0.0
        self._tremor = (0.0, 0.0)

    def reset(self) -> None:
        self._overshoot_left = 0
        self._phase = 0.0
        self._tremor = (0.0, 0.0)

    def _tremor_step(self) -> tuple[float, float]:
        p = self.p
        self._phase += 2.0 * math.pi * p.tremor_freq / 120.0
        # Small random walk so the jitter is band-limited rather than white.
        tx = 0.7 * self._tremor[0] + self._rng.uniform(-1.0, 1.0)
        ty = 0.7 * self._tremor[1] + self._rng.uniform(-1.0, 1.0)
        self._tremor = (tx, ty)
        return tx * p.tremor_px, ty * p.tremor_px

    def step(self, dx: float, dy: float) -> tuple[int, int]:
        """Return a relative mouse step for a pixel error."""
        p = self.p
        dist = math.hypot(dx, dy)

        if dist <= p.deadzone:
            tx, ty = self._tremor_step()
            wx = int(round(tx))
            wy = int(round(ty))
            return wx, wy

        gain = p.move_fraction
        if dist > p.overshoot_error and self._overshoot_left == 0:
            self._overshoot_left = p.overshoot_frames
        if self._overshoot_left > 0:
            gain *= p.overshoot_factor
            self._overshoot_left -= 1

        mx = dx * gain
        my = dy * gain

        tx, ty = self._tremor_step()
        mx += tx
        my += ty

        sx = max(-p.max_step, min(p.max_step, int(round(mx))))
        sy = max(-p.max_step, min(p.max_step, int(round(my))))

        if abs(sx) < p.min_move and dist > p.deadzone:
            sx = int(math.copysign(min(p.min_move, p.max_step), dx or 1))
        if abs(sy) < p.min_move and dist > p.deadzone:
            sy = int(math.copysign(min(p.min_move, p.max_step), dy or 1))

        return sx, sy


def split_segments(dx: int, dy: int, max_segment: int = 127) -> list[tuple[int, int]]:
    """Split a large delta into HID-sized segments (each axis <= 127)."""
    segments: list[tuple[int, int]] = []
    rx, ry = int(dx), int(dy)
    while rx != 0 or ry != 0:
        sx = max(-max_segment, min(max_segment, rx))
        sy = max(-max_segment, min(max_segment, ry))
        segments.append((sx, sy))
        rx -= sx
        ry -= sy
    return segments
