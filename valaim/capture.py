import numpy as np

from .input_ctrl import get_cursor_pos


class DxcamCapture:
    """Drop-in for ScreenCapture using DXGI desktop duplication (1-4ms/frame
    vs mss ~6-12ms). Falls back is handled by the caller: if dxcam cannot be
    imported or created, ScreenCapture stays in charge."""

    def __init__(self, base):
        import dxcam

        self._base = base
        self._cam = dxcam.create(color_mode="BGR")
        self._dup = self._cam  # duplication keeps running; grab() pulls one frame

    def region(self):
        return self._base.region()

    def crosshair(self):
        return self._base.crosshair()

    def monitor(self):
        return self._base.monitor

    @property
    def monitors(self):
        return self._base.monitors

    @property
    def crop_size(self):
        return self._base.crop_size

    @crop_size.setter
    def crop_size(self, v):
        self._base.crop_size = v

    def grab(self):
        x, y, w, h = self._base.region()
        frame = self._dup.grab()
        while frame is None:
            frame = self._dup.grab()
        return frame[y:y + h, x:x + w, :3].copy(), x, y, w, h


class ScreenCapture:
    def __init__(
        self,
        monitor: int = 0,
        mode: str = "center",
        crop_size: int = 640,
        anchor: str = "crosshair",
    ):
        import mss

        self.sct = mss.mss()
        self.monitors = self.sct.monitors
        self.monitor = monitor
        self.mode = mode
        self.crop_size = crop_size
        self.anchor = anchor

    def _monitor_rect(self) -> tuple[int, int, int, int]:
        idx = self.monitor + 1 if self.monitor >= 0 else self.monitor
        try:
            mon = self.monitors[idx]
        except IndexError:
            mon = self.monitors[0]
        return int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])

    def crosshair(self) -> tuple[int, int]:
        """The aim reference point.

        Valorant locks the crosshair to the centre of the game window, so the
        centre of the monitor is used instead of the OS cursor. The OS cursor is
        only a reliable reference when the game is not focused, which is exactly
        why aiming used to work only after alt-tabbing out.
        """
        if self.anchor == "cursor":
            return get_cursor_pos()
        left, top, width, height = self._monitor_rect()
        return left + width // 2, top + height // 2

    def region(self) -> tuple[int, int, int, int]:
        left, top, width, height = self._monitor_rect()

        if self.mode == "full":
            return left, top, width, height

        crop = min(self.crop_size, min(width, height))
        half = crop // 2
        x, y = self.crosshair()
        left_x = max(left, min(x - half, left + width - crop))
        top_y = max(top, min(y - half, top + height - crop))
        return left_x, top_y, crop, crop

    def grab(self) -> tuple[np.ndarray, int, int, int, int]:
        x, y, w, h = self.region()
        shot = self.sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.array(shot, dtype=np.uint8)[:, :, :3]
        return img, x, y, w, h
