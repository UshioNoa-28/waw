"""Zero out VALORANT HUD rectangles before inference (kills corner/bottom
phantom heads: minimap, killfeed, ability bar, own weapon model)."""
import ctypes

# normalized (x0,y0,x1,y1) full-screen rects
RECTS = [
    (0.00, 0.000, 0.175, 0.150),   # minimap top-left
    (0.655, 0.000, 1.000, 0.130),  # killfeed top-right
    (0.400, 0.000, 0.600, 0.055),  # round timer top-center
    (0.000, 0.865, 1.000, 1.000),  # ability bar + ammo + own weapon bottom
]


def apply(img, crop_x: int, crop_y: int) -> None:
    try:
        user32 = ctypes.windll.user32
        W = user32.GetSystemMetrics(0)
        H = user32.GetSystemMetrics(1)
    except Exception:
        return
    h, w = img.shape[0], img.shape[1]
    for x0, y0, x1, y1 in RECTS:
        sx0 = max(0, int(x0 * W - crop_x))
        sy0 = max(0, int(y0 * H - crop_y))
        sx1 = min(w, int(x1 * W - crop_x))
        sy1 = min(h, int(y1 * H - crop_y))
        if sx1 > sx0 and sy1 > sy0:
            img[sy0:sy1, sx0:sx1] = 0
