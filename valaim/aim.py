import math
from dataclasses import dataclass

from .backend import Detection
from .config import AimConfig


@dataclass
class Target:
    det: Detection
    x: float
    y: float
    distance: float
    score: float


class TargetSelector:
    def __init__(self, cfg: AimConfig):
        self.cfg = cfg
        self.locked: Target | None = None

    def _allowed(self, det: Detection) -> bool:
        mh = getattr(self.cfg, "min_head_px", 0.0)
        if mh > 0:
            est_head_w = det.w if self._is_head(det) else det.w * 0.45
            if est_head_w < mh:
                return False
        name = det.name.lower()
        if self.cfg.target_classes and name not in {c.lower() for c in self.cfg.target_classes}:
            return False
        if self.cfg.exclude_classes and name in {c.lower() for c in self.cfg.exclude_classes}:
            return False
        return True

    def _is_head(self, det: Detection) -> bool:
        return "head" in det.name.lower()

    def _aim_point(self, det: Detection) -> tuple[float, float]:
        cx = det.x + det.w / 2.0
        off = self.cfg.head_offset_y
        if self._is_head(det):
            # Aim inside the head box; head_offset_y nudges it further down if
            # the detector's head box sits above the real hitbox.
            return cx, det.y + det.h * self.cfg.head_bias + off
        if self.cfg.aim_mode == "head":
            return cx, det.y + det.h * self.cfg.head_height + off
        if self.cfg.aim_mode == "head_wide":
            return cx, det.y + det.h * self.cfg.head_width + off
        return cx, det.y + det.h * self.cfg.aim_height + off

    def select(self, detections: list[Detection], cursor: tuple[int, int]) -> Target | None:
        best: Target | None = None

        # sticky lock: keep chasing the box we locked onto (within 45px of its
        # last aim point) while it is still detected. Without this, a row of
        # range dummies hands the lock to the nearest neighbour mid-flick and
        # the stroke reverses ("round-trip" seen in real13/real15/real17).
        keep = self.locked
        if keep is not None:
            best_match: Target | None = None
            for det in detections:
                if not self._allowed(det):
                    continue
                x, y = self._aim_point(det)
                drift = math.hypot(x - keep.x, y - keep.y)
                if drift < 25.0 and (best_match is None or drift < best_match.distance):
                    best_match = Target(det=det, x=x, y=y, distance=drift, score=99.0)
            if best_match is None:
                self.locked = None
            else:
                self.locked = best_match
                best_match.distance = math.hypot(best_match.x - cursor[0], best_match.y - cursor[1])
                return best_match

        for det in detections:
            if not self._allowed(det):
                continue

            x, y = self._aim_point(det)
            distance = math.hypot(x - cursor[0], y - cursor[1])

            if distance > self.cfg.fov_radius:
                continue

            score = det.conf - 0.0005 * distance
            if self._is_head(det):
                score += self.cfg.head_boost

            if best is None or score > best.score:
                best = Target(det=det, x=x, y=y, distance=distance, score=score)

        self.locked = best
        return best

    def explain(self, detections: list[Detection], cursor: tuple[int, int]) -> str:
        if not detections:
            return "no detections"

        allowed = [det for det in detections if self._allowed(det)]
        if not allowed:
            return f"{len(detections)} detected, 0 in aim class"

        nearest: Target | None = None
        for det in allowed:
            x, y = self._aim_point(det)
            distance = math.hypot(x - cursor[0], y - cursor[1])
            if nearest is None or distance < nearest.distance:
                nearest = Target(det=det, x=x, y=y, distance=distance, score=0.0)

        det, dist = nearest.det, nearest.distance
        if dist > self.cfg.fov_radius:
            return f"nearest {det.name} {dist:.0f}px away, fov {self.cfg.fov_radius}px"

        score = det.conf - 0.0005 * dist
        if self._is_head(det):
            score += self.cfg.head_boost
        return f"target {det.name} conf={det.conf:.2f} dist={dist:.0f}px score={score:.2f}"
