import numpy as np

from .input_ctrl import get_cursor_pos


class DxcamCapture:
    """DXGI desktop duplication capture of a FIXED region (2-4ms/frame vs
    mss ~8-16ms on this class of laptop). Re-creates the duplication if the
    region or crop size changes (panel hot-edits)."""

    def __init__(self, base):
        import dxcam

        self._base = base
        self._cam = None
        self._dead = False
        self._last = None
        self._open()

    def _open(self) -> None:
        import dxcam

        x, y, w, h = self._base.region()
        self._key = (x, y, w, h)
        self._cam = dxcam.create(output_color="BGR", region=(x, y, x + w, y + h))

    def region(self):
        return self._base.region()

    def crosshair(self):
        return self._base.crosshair()

    @property
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
        if self._dead:
            return self._base.grab()
        try:
            if (x, y, w, h) != self._key:
                self._cam.release()
                self._open()
            frame = None
            getter = getattr(self._cam, "get_frame", None)
            if getter is not None:
                try:
                    frame = getter(blocking=True, timeout=0.1)
                except TypeError:
                    frame = getter()
            if frame is None:
                frame = self._cam.grab()
            if frame is not None:
                self._last = frame[:, :, :3]
            # static desktop == no new presents; the previous frame IS the
            # current truth - reuse instead of dying
            if self._last is None:
                raise TimeoutError("duplication has not produced a frame yet")
            return self._last.copy(), x, y, w, h
        except Exception as exc:
            # exclusive-fullscreen games kill duplication; never let the aim
            # loop die over a capture optimization -> permanent mss downgrade
            self._dead = True
            try:
                self._cam.release()
            except Exception:
                pass
            print(f"[capture] duplication lost ({exc.__class__.__name__}); downgraded to mss for this session")
            return self._base.grab()


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
