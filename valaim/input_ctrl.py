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
        "LALT": 0xA4, "RALT": 0xA5, "ALT": 0xA4,
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
# "bt"        -> send relative moves to the BtAimBridge Android app over TCP;
#              the phone re-emits them as a real Bluetooth HID mouse. Vanguard
#              sees a hardware mouse, not synthetic input.
# "sendinput" -> classic user32 injection (dropped while a Vanguard game is
#              focused).
# "auto"      -> use bt when a host is configured, else sendinput.
# ---------------------------------------------------------------------------

_BACKEND = "auto"
_BT = None
_RAW = None
_RAW_TRIED = False


def _ensure_raw_input() -> None:
    """Start the raw input sink thread (Windows). Safe to call repeatedly."""
    global _RAW, _RAW_TRIED
    if sys.platform != "win32":
        return
    if _RAW is not None:
        return
    if globals().get("_RAW_TRIED"):
        return
    _RAW_TRIED = True
    try:
        from . import rawinput as _ri

        if _ri.start():
            _RAW = _ri
            print("[input] raw input sink active (button/key reads work while the game is focused)")
        else:
            try:
                from .rawinput import failure_reason
                why = failure_reason()
            except Exception:
                why = ""
            print(f"[input] raw input sink unavailable ({why}); falling back to GetAsyncKeyState", file=sys.stderr)
    except Exception as exc:
        print(f"[input] raw input init failed ({exc})", file=sys.stderr)


def foreground_process() -> str:
    """Name (not path) of the current foreground window's process, '' if unknown."""
    if sys.platform != "win32":
        return ""
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def raw_input_state() -> tuple[bool, list[str]]:
    """(active, recent events). Starts the sink on first call."""
    _ensure_raw_input()
    if _RAW is None:
        return False, []
    return True, _RAW.recent_events()


def set_backend(name: str, bt_host: str | None = None, bt_port: int = 47800) -> str:
    """Select the input backend; returns the one actually active.

    bt_host may come from --bt-host. If it is None, the BT_BRIDGE_HOST
    environment variable is used.
    """
    global _BACKEND, _BT
    name = (name or "auto").lower()
    if name not in {"auto", "bt", "sendinput"}:
        raise ValueError(f"Unknown input backend: {name}")

    _ensure_raw_input()

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
            print(f"[input] bt unavailable ({exc}); falling back to sendinput", file=sys.stderr)

    _BACKEND = "sendinput"
    return _BACKEND


def active_backend() -> str:
    return _BACKEND


def close_backend() -> None:
    global _BT
    if _BT is not None:
        try:
            _BT.close()
        except Exception:
            pass
        _BT = None


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
    _send_input_move(int(dx), int(dy))


def press_key(key: str) -> bool:
    if sys.platform != "win32":
        return False
    vk = _vk(key)
    if _RAW is not None:
        return _RAW.key_down(vk)
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

_MOUSE_VK = {
    "left": VK_LBUTTON,
    "right": VK_RBUTTON,
    "middle": VK_MBUTTON,
    "x1": VK_XBUTTON1,
    "x2": VK_XBUTTON2,
}


def bt_snap_events() -> int:
    """Number of 'lock and fire' presses from the phone big button."""
    if _BT is not None:
        try:
            return _BT.pop_snap_flips()
        except Exception:
            return 0
    return 0


def bt_armed(on: bool) -> None:
    """Tell the phone the assist is live (in-game, enabled) - only then gate LMB."""
    if _BT is not None:
        try:
            _BT.set_armed(on)
        except Exception:
            pass


def bt_ping_hid() -> bool:
    if _BT is not None:
        try:
            return bool(_BT.ping_hid())
        except Exception:
            return False
    return False


def bt_hid_stats() -> tuple[bool, float | None]:
    if _BT is not None:
        return (bool(getattr(_BT, "_hid_ready", False)), getattr(_BT, "_hid_rtt", None))
    return (False, None)


def bt_send_line(line: str) -> None:
    if _BT is not None:
        try:
            _BT.send_line(line)
        except Exception:
            pass


def bt_pop_ping_rtt():
    if _BT is not None:
        try:
            return _BT.pop_ping_rtt()
        except Exception:
            return None
    return None


def bt_lmb_held() -> bool:
    """Phone reports the user is holding left mouse (proxy gate)."""
    if _BT is not None:
        try:
            return bool(_BT.lmb_held())
        except Exception:
            return False
    return False


def bt_nail(on: bool) -> None:
    """Tell the phone whether the crosshair is nailed (mouse-proxy LMB gate)."""
    if _BT is not None:
        try:
            _BT.set_nail(on)
        except Exception:
            pass


def bt_lock_events() -> int:
    """Number of lock-toggle presses received from the phone app."""
    if _BT is not None:
        try:
            return _BT.pop_lock_flips()
        except Exception:
            return 0
    return 0


def mouse_button_down(button: str) -> bool:
    """True while the given physical mouse button is held (read-only).

    Uses the raw input sink (survives the game consuming input) when it is up,
    otherwise GetAsyncKeyState.
    """
    if sys.platform != "win32":
        return False
    name = button.lower()
    if name not in _MOUSE_VK:
        raise ValueError(f"Unsupported mouse button: {button}")
    if _RAW is not None:
        return _RAW.mouse_button_down(name)
    return bool(user32.GetAsyncKeyState(_MOUSE_VK[name]) & 0x8000)


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
    if _RAW is not None:
        return 0x8000 if _RAW.key_down(vk) else 0
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

    flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    if not down:
        flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    user32.mouse_event(flag, 0, 0, 0, ctypes.c_ulong(0))
