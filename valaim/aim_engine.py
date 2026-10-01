"""Aim shaping: turn a per-frame pixel error into a mouse step.

Design goals for this build: **stability**, not human-likeness.

  * **Sub-pixel accumulation** - fractional remainders are carried to the next
    frame, so small errors are not rounded away to zero.
  * **Error smoothing** - an exponential moving average damps the frame-to-frame
    jitter of the model's bounding box, so the crosshair does not twitch on a
    static target.
  * **Slew limit** - the per-frame step is capped in pixels, so a large or noisy
    error cannot fling the crosshair past the target (no more "jumps to the
    head and overshoots").
  * **Dead zone** - inside a small radius, nothing is emitted.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass


@dataclass
class AimParams:
    move_fraction: float = 0.4    # P: fraction of the error per frame. Safe up
                                  # to ~0.45 only together with in-flight comp
                                  # (comp_frames); without comp keep <= 0.25.
    max_step: int = 60            # hard cap on mouse counts per frame
    min_move: float = 1.0
    min_speed: float = 2.0        # counts/frame floor outside the deadzone
    comp_weight: float = 1.0      # fraction of in-flight move pre-subtracted from error
    comp_frames: int = 8          # in-flight window, loop frames

    deadzone: float = 4.0         # stop radius (px), fixed mode / fallback
    dz_frac: float = 0.0          # deadzone = frac of head-box width (0=fixed); clamped 2..12
    slew_px: float = 0.0          # if >0, cap per-frame smoothed-target slew to this (px)
    burst: bool = False           # one full stroke per observation, then silence
    burst_cooldown: float = 0.15  # s of enforced silence after a burst (>actuation lag)
    burst_gain: float = 1.0       # fraction of error covered by the single stroke
    burst_min_px: float = 0.0     # (legacy) ignore strokes below this error
    settle_ms: float = 90.0       # hold fire until the lock has been stable this long
    settle_px: float = 45.0       # absolute fallback when box width unknown
    settle_frac: float = 1.6      # settle threshold = frac * head-box width (scale-invariant)
    burst_early: bool = True      # fire the next stroke as soon as the last one is SEEN to land (instead of waiting the full cooldown)
    arrive_px: float = 8.0        # lock OFF the output inside this radius (0=off)
    resume_px: float = 32.0       # ...and only resume past this (above spike band)
    med_win: int = 1              # median filter width on raw error (1=off)
    smoothing: float = 0.6        # EMA weight for new error (0=ignore,1=raw)

    # Humanization (measured from aim_lab: 60 trials, jit med 3.1px, bell rise,
    # reaction included in 623ms median flick). Off unless enabled.
    humanize: bool = False
    react_ms: tuple = (120.0, 220.0)   # hold-off after a NEW target lock
    ramp_s: float = 0.09               # gain ramps 40% -> 100% over this
    tremor_px: float = 2.2             # OU tremor steady-state std (px)
    tremor_hz: float = 10.0            # action-tremor band
    seed: float = 0.0

    max_segment: int = 127
    # Mouse counts emitted per screen pixel of error. Depends on the in-game
    # sensitivity; 1.0 is a wrong default for most games, so the app
    # auto-calibrates this at startup (see main.calibrate_gain).
    counts_per_px: float = 1.0


class AimEngine:
    """Stateful shaper. One instance per session."""

    def __init__(self, params: AimParams | None = None, seed: int | None = None) -> None:
        import random as _r
        self.p = params or AimParams()
        self._rng = _r.Random(seed or None)
        self._lock_at: float | None = None   # when current lock began (reaction gate)
        self._gate = 0.0                      # gate length, s
        self._tx = self._ty = 0.0             # OU tremor state
        self._ph = 0.0
        self._sx: float | None = None   # smoothed error x
        self._sy: float | None = None
        self._carry_x = 0.0             # sub-pixel remainder
        self._carry_y = 0.0
        self._hist = []               # (t, px_x, px_y) commands not yet rendered
        self._raw: list = []          # recent raw errors for median filter
        self._latched = False         # arrived: output suppressed
        self._burst_t = -9e9
        self._last_obs: tuple | None = None
        self._bexp = 0.0      # expected px drop of the in-flight stroke
        self._bref = None     # observation at fire time (for landing detection)
        self._bpx = None   # previous raw observation (spike clamp ref)
        self._bacc: list = []   # (t, dx, dy) post-flight observation accumulator
        self._dbg = None              # (smx, smy, inflx, infly, rawx, rawy)

    def reset(self) -> None:
        self._sx = self._sy = None
        self._carry_x = self._carry_y = 0.0
        self._hist = []
        self._raw = []
        self._latched = False
        self._lock_at = None

    def on_new_lock(self) -> None:
        """Caller signals a fresh target (or a re-acquire after a gap)."""
        self._lock_at = time.monotonic()
        self._latched = False
        # Stale in-flight history / smoothing state from the PREVIOUS target
        # makes the first command point the wrong way ("甩出去再回来").
        self._hist = []
        self._raw = []
        self._carry_x = self._carry_y = 0.0
        self._sx = self._sy = None
        self._bpx = None
        lo = min(self.p.react_ms) / 1000.0
        hi = max(self.p.react_ms) / 1000.0
        self._gate = self._rng.uniform(lo, hi) if self.p.humanize else 0.0

    def _tremor(self, dt: float) -> tuple[float, float]:
        """Band-limited OU noise: theta=2*pi*hz, sigma set so std=tremor_px."""
        import math as _m
        if self.p.tremor_hz <= 0 or self.p.tremor_px <= 0:
            return 0.0, 0.0
        theta = 2.0 * _m.pi * self.p.tremor_hz
        alpha = _m.exp(-theta * dt)
        sigma = self.p.tremor_px * _m.sqrt(2.0 * theta)   # OU stationary std
        scale = sigma * _m.sqrt(max(0.0, (1.0 - alpha * alpha) / (2.0 * theta))) * dt
        self._tx = self._tx * alpha + self._rng.gauss(0.0, 1.0) * scale
        self._ty = self._ty * alpha + self._rng.gauss(0.0, 1.0) * scale
        return self._tx, self._ty

    def step(self, dx: float, dy: float,
             lead_x: float = 0.0, lead_y: float = 0.0,
             box_w: float = 0.0) -> tuple[int, int]:
        """Return a relative mouse step for the current pixel error.

        lead_x/lead_y are caller-computed target-motion predictions (px); they
        are added AFTER smoothing so target velocity never feeds back through
        our own actuation latency (which is what made a measurement-derivative
        term unstable here).
        """
        p = self.p
        dz = p.deadzone
        if p.dz_frac > 0 and box_w > 0:
            dz = max(2.0, min(12.0, p.dz_frac * box_w))

        # Median filter first: kills single-frame outlier jumps of the box.
        if p.med_win >= 3:
            self._raw.append((dx, dy))
            if len(self._raw) > p.med_win:
                self._raw.pop(0)
            xs = sorted(v[0] for v in self._raw)
            ys = sorted(v[1] for v in self._raw)
            dx = xs[len(xs) // 2]
            dy = ys[len(ys) // 2]

        # Arrival latch (hysteresis): once on target, hold position until the
        # error clearly exceeds resume_px. Humans stop micro-correcting noise;
        # chasing model jitter is exactly the "末端晃动" complaint.
        raw_dist = math.hypot(dx, dy)
        use_latch = p.arrive_px > 0
        if use_latch and self._latched:
            if raw_dist <= p.resume_px:
                self._carry_x = self._carry_y = 0.0
                return 0, 0
            self._latched = False

        # Exponential moving average of the target error (damps model jitter).
        if self._sx is None:
            self._sx, self._sy = dx, dy
        else:
            a = max(0.05, min(1.0, p.smoothing))
            # In-game head-box jitter has p90 ~22px single-frame jumps while
            # real motion is gradual: trust big one-frame jumps slowly, so the
            # crosshair does not chase detection noise (measured data).
            jx, jy = dx - self._sx, dy - self._sy
            jump = math.hypot(jx, jy)
            if jump > 12.0:
                a *= 0.25
            if p.slew_px > 0 and jump > p.slew_px:
                k = p.slew_px / jump
                dx, dy = self._sx + jx * k, self._sy + jy * k
            self._sx += a * (dx - self._sx)
            self._sy += a * (dy - self._sy)

        # Burst mode: one complete stroke per observation window. The next
        # stroke fires either when the previous one is SEEN to land (early,
        # evidence-based) or when the cooldown elapses (fallback, covers the
        # measured D90=267ms network tail).
        if p.burst:
            nowb = time.monotonic()
            if use_latch and raw_dist <= p.arrive_px:
                self._latched = True
                self._carry_x = self._carry_y = 0.0
                return 0, 0
            cp0 = max(0.05, p.counts_per_px)
            # accumulate post-flight observations, skip spike frames
            if self._bpx is not None and math.hypot(dx - self._bpx[0], dy - self._bpx[1]) > 26.0:
                spike = True
            else:
                spike = False
                self._bacc.append((nowb, dx, dy))
                if len(self._bacc) > 12:
                    self._bacc.pop(0)
            self._bpx = (dx, dy)

            landed = False
            if p.burst_early and self._bref is not None and self._bexp > 25.0 \
                    and nowb - self._burst_t >= 0.025 \
                    and math.hypot(dx - self._bref[0], dy - self._bref[1]) >= 0.45 * self._bexp:
                landed = True          # previous stroke visibly arrived

            distb = math.hypot(self._sx, self._sy)
            obs_moved = 0.0
            if self._last_obs is not None:
                obs_moved = math.hypot(dx - self._last_obs[0], dy - self._last_obs[1])
            self._last_obs = (dx, dy)
            # Stability gate: a mid-flick re-lock (crosshair sweeping past an
            # enemy) churns within a couple frames; a real acquire settles.
            # Fire (and even collect aim frames) only after the hand lands.
            thr = p.settle_px
            if p.settle_frac > 0 and box_w > 0:
                thr = max(15.0, p.settle_frac * box_w)
            settled = True if self._lock_at is None else (nowb - self._lock_at >= p.settle_ms / 1000.0 and obs_moved < thr)
            if not settled:
                self._bacc = []
            if landed or distb > dz:
                if not landed and not settled:
                    return 0, 0
                if p.burst_min_px and distb < p.burst_min_px:
                    return 0, 0
            sx = sy = 0.0
            if landed:
                sx, sy = dx, dy        # aim at the fresh landed observation
            elif distb <= dz or nowb - self._burst_t < p.burst_cooldown:
                return 0, 0
            else:
                pool = [(t_, ox, oy) for (t_, ox, oy) in self._bacc
                        if t_ >= self._burst_t + 0.08][-3:]
                if pool:
                    sx = sum(o[1] for o in pool) / len(pool)
                    sy = sum(o[2] for o in pool) / len(pool)
                else:
                    sx, sy = dx, dy
            g = max(0.05, min(1.5, p.burst_gain))
            # moving-target compensation: caller-supplied velocity prediction
            # (guarded against detection spikes upstream)
            bx = (sx + lead_x) * g * cp0
            by = (sy + lead_y) * g * cp0
            lim = max(1, p.max_step)   # phone HID layer splits >127 itself
            magb = math.hypot(bx, by)
            if magb > lim:
                sc = lim / magb
                bx, by = bx * sc, by * sc
            ix, iy = int(round(bx)), int(round(by))
            if ix or iy:
                self._burst_t = nowb
                self._bref = (sx, sy)
                self._bexp = math.hypot(ix, iy) / cp0
                self._hist.append((nowb, ix / cp0, iy / cp0))
            self._bacc = [] if landed or (ix or iy) else self._bacc
            return ix, iy

        # Smith-predictor style compensation: our commands only appear in the
        # screenshot after the loop delay, so subtract everything still in
        # flight; otherwise the loop re-sends moves it already ordered.
        cp0 = max(0.05, p.counts_per_px)
        now = time.monotonic()
        window = max(1, p.comp_frames or 8) / 60.0
        self._hist = [h for h in self._hist if now - h[0] <= window]
        w = max(0.0, min(1.0, p.comp_weight))
        infl_x = w * sum(h[1] for h in self._hist)
        infl_y = w * sum(h[2] for h in self._hist)
        raw_ex = self._sx + lead_x
        raw_ey = self._sy + lead_y
        ex = raw_ex - infl_x
        ey = raw_ey - infl_y
        self._dbg = (self._sx, self._sy, infl_x, infl_y, raw_ex, raw_ey)
        # Compensation must never flip the commanded direction: over-estimating
        # the in-flight move shows up as a backward kick that then has to be
        # corrected - the exact "always overshoots outward first" signature.
        if raw_ex * ex < 0:
            ex = 0.0
        if raw_ey * ey < 0:
            ey = 0.0
        dist = math.hypot(self._sx, self._sy)

        if use_latch and raw_dist <= p.arrive_px:
            self._latched = True
            self._carry_x = self._carry_y = 0.0
            return 0, 0
        if dist <= dz:
            self._carry_x = self._carry_y = 0.0
            return 0, 0

        gain = p.move_fraction
        # Humanization: reaction gate, gain ramp-in (bell-ish onset), tremor.
        if p.humanize and self._lock_at is not None:
            since = time.monotonic() - self._lock_at
            if since < self._gate:
                return 0, 0
            if p.ramp_s > 0 and since < self._gate + p.ramp_s:
                f = (since - self._gate) / p.ramp_s
                gain *= 0.4 + 0.6 * f
            jx, jy = self._tremor(max(0.004, min(0.05, time.monotonic() - self._last_t if self._last_t else 0.016)))
            self._last_t = time.monotonic()
            ex += jx
            ey += jy

        # Pixel error -> mouse counts using the calibrated gain.
        cp = cp0
        step_x = ex * gain * cp + self._carry_x
        step_y = ey * gain * cp + self._carry_y

        # Slew limit (also respects the HID report's +-127 range).
        limit = max(1, min(p.max_step, p.max_segment))
        mag = math.hypot(step_x, step_y)
        if mag > limit:
            scale = limit / mag
            step_x *= scale
            step_y *= scale
            # The clamp is not a queue: dropped remainder must NOT wind up
            # into the carry or the crosshair jumps when the error shrinks.
            self._carry_x = self._carry_y = 0.0

        ix = int(round(step_x))
        iy = int(round(step_y))

        # Speed floor: outside the deadzone never crawl below min_speed,
        # otherwise the final stretch feels sluggish (exponential tail).
        floor = max(0, p.min_speed)
        if floor and dist > dz:
            mag0 = math.hypot(ix, iy)
            if 0.0 < mag0 < floor:
                s_ = floor / mag0
                ix, iy = int(round(ix * s_)), int(round(iy * s_))
            elif mag0 == 0.0 and (ex != 0.0 or ey != 0.0):
                em = math.hypot(ex, ey) or 1.0
                ix = int(round(floor * ex / em))
                iy = int(round(floor * ey / em))

        self._carry_x = step_x - ix if abs(step_x) >= floor or ix == 0 else 0.0
        self._carry_y = step_y - iy if abs(step_y) >= floor or iy == 0 else 0.0
        if ix or iy:
            self._hist.append((now, ix / cp, iy / cp))

        return ix, iy


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
