import os
import sys
import time

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", INPUT_UNION),
        ]

    INPUT_MOUSE = 0

    VK = {
        "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
        "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
        "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
        "SPACE": 0x20,
        "LSHIFT": 0xA0, "RSHIFT": 0xA1,
        "LCTRL": 0xA2, "RCTRL": 0xA3,
        "LALT": 0xA4, "RALT": 0xA5,
        "TAB": 0x09, "ENTER": 0x0D,
    }
    for code, name in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 0x41):
        VK[name] = code
    for code, name in enumerate("0123456789", 0x30):
        VK[name] = code


def _vk(key: str) -> int:
    key = key.upper()
    if sys.platform != "win32" or key not in globals().get("VK", {}):
        raise ValueError(f"Unsupported keybind: {key}")
    return VK[key]


def get_cursor_pos() -> tuple[int, int]:
    if sys.platform != "win32":
        return (0, 0)
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


# ---------------------------------------------------------------------------
# Input backend selection
#
# "bt"    -> send relative moves to the BtAimBridge Android app over TCP; the
#            phone re-emits them as a real Bluetooth HID mouse. Vanguard sees
#            a hardware mouse, not synthetic input.
# "vhid"  -> write reports into the VHF virtual mouse driver's shared ring.
# "sendinput" -> classic user32 injection (dropped while a Vanguard game is
#            focused).
# "auto"  -> try bt, then vhid, then sendinput.
# ---------------------------------------------------------------------------

_BACKEND = "auto"
_VHID = None
_BT = None


def set_backend(name: str, bt_host: str | None = None, bt_port: int = 47800) -> str:
    """Select the input backend; returns the one actually active.

    bt_host may come from --bt-host. If it is None, the BT_BRIDGE_HOST
    environment variable is used.
    """
    global _BACKEND, _VHID, _BT
    name = (name or "auto").lower()
    if name not in {"auto", "bt", "vhid", "sendinput"}:
        raise ValueError(f"Unknown input backend: {name}")

    if bt_host is None:
        bt_host = os.environ.get("BT_BRIDGE_HOST")

    if name in {"auto", "bt"} and bt_host:
        try:
            from .bt_bridge import BtBridge

            _BT = BtBridge(bt_host, bt_port)
            _BACKEND = "bt"
            return _BACKEND
        except Exception as exc:
            if name == "bt":
                raise
            print(f"[input] bt unavailable ({exc}); trying next backend", file=sys.stderr)

    if name in {"auto", "vhid"}:
        try:
            from .hid_mouse import VhidMouse

            _VHID = VhidMouse()
            _BACKEND = "vhid"
            return _BACKEND
        except Exception as exc:
            if name == "vhid":
                raise
            print(f"[input] vhid unavailable ({exc}); using sendinput", file=sys.stderr)

    _VHID = None
    _BACKEND = "sendinput"
    return _BACKEND


def active_backend() -> str:
    return _BACKEND


def close_backend() -> None:
    global _VHID, _BT
    if _BT is not None:
        try:
            _BT.close()
        except Exception:
            pass
        _BT = None
    if _VHID is not None:
        try:
            _VHID.close()
        except Exception:
            pass
        _VHID = None


def _send_input_move(dx: int, dy: int) -> None:
    inp = INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(
            mi=MOUSEINPUT(
                dx=int(dx),
                dy=int(dy),
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=None,
            )
        ),
    )
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        user32.mouse_event(MOUSEEVENTF_MOVE, int(dx), int(dy), 0, ctypes.c_ulong(0))


def move_mouse(dx: int, dy: int) -> None:
    if sys.platform != "win32":
        return
    if _BT is not None:
        _BT.move(int(dx), int(dy))
        return
    if _VHID is not None:
        _VHID.move(int(dx), int(dy))
        return
    _send_input_move(int(dx), int(dy))


def press_key(key: str) -> bool:
    if sys.platform != "win32":
        return False
    return bool(user32.GetAsyncKeyState(_vk(key)) & 0x8000)


def key_state(key: str) -> int:
    """Raw async key state (low 16 bits). 0x8000 = pressed down.

    Returns -1 when key polling is unavailable (not on Windows) and
    -2 when the key name is not recognized.
    """
    if sys.platform != "win32":
        return -1
    vk = globals().get("VK", {}).get(key.upper())
    if vk is None:
        return -2
    return int(user32.GetAsyncKeyState(vk)) & 0xFFFF


def set_mouse_button(button: str, down: bool) -> None:
    if sys.platform != "win32":
        return
    button = button.lower()
    if button not in {"left", "right"}:
        raise ValueError(f"Unsupported mouse button: {button}")

    if _BT is not None:
        from .bt_bridge import BTN_LEFT, BTN_RIGHT

        bit = BTN_LEFT if button == "left" else BTN_RIGHT
        _BT._button_mask = (getattr(_BT, "_button_mask", 0) | bit) if down \
            else (getattr(_BT, "_button_mask", 0) & ~bit)
        _BT.set_buttons(_BT._button_mask)
        return

    if _VHID is not None:
        from .hid_mouse import BTN_LEFT, BTN_RIGHT

        bit = BTN_LEFT if button == "left" else BTN_RIGHT
        current = _VHID.button_state
        _VHID.button_state = current | bit if down else current & ~bit
        return

    flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    if not down:
        flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    user32.mouse_event(flag, 0, 0, 0, ctypes.c_ulong(0))
