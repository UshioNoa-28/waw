"""Global input reader via Raw Input (RIDEV_INPUTSINK).

GetAsyncKeyState is unreliable while a game that uses raw input has focus:
the game consumes the device events and the async key states stay stale.
This module registers our own hidden window with RIDEV_INPUTSINK so Windows
delivers a *copy* of every mouse/keyboard event to us without hooks, injection
or interference. All calls are documented user32 APIs.

Thread-safe: a dedicated thread owns the message loop and updates shared
state; readers just query it.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100

RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1

MOUSE_DOWN = {
    0x0001: "left",
    0x0004: "right",
    0x0010: "middle",
    0x0040: "x1",
    0x0100: "x2",
}
MOUSE_UP = {
    0x0002: "left",
    0x0008: "right",
    0x0020: "middle",
    0x0080: "x1",
    0x0200: "x2",
}

user32 = None
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.GetRawInputData.argtypes = [
        wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
        ctypes.POINTER(wintypes.UINT), wintypes.UINT
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


_HEADER = ctypes.sizeof(RAWINPUTHEADER)  # 24 on x64


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.buttons: set[str] = set()
        self.keys: set[int] = set()
        self.ok = False
        self.events: list[str] = []

    def _log(self, text: str) -> None:
        # Keep a short ring of recent events for the GUI diagnostics panel.
        self.events.append(f"{time.strftime('%H:%M:%S')} {text}")
        if len(self.events) > 60:
            del self.events[:-30]

    def mouse_down(self, name: str) -> bool:
        with self.lock:
            return name in self.buttons

    def key_down(self, vk: int) -> bool:
        with self.lock:
            return int(vk) in self.keys

    def recent(self) -> list[str]:
        with self.lock:
            return list(reversed(self.events[-12:]))


_state = _State()
_state_reason = ""
_started = False
_thread: threading.Thread | None = None
_proc_ref = None


def available() -> bool:
    return _state.ok


def failure_reason() -> str:
    return _state_reason


def start() -> bool:
    """Launch the input sink thread (idempotent). Windows only."""
    global _started, _thread, _proc_ref
    if _started:
        return _state.ok
    _started = True

    _thread = threading.Thread(target=_run, name="raw-input-sink", daemon=True)
    _thread.start()
    for _ in range(40):
        if _state.ok:
            return True
        threading.Event().wait(0.025)
    return False


def mouse_button_down(name: str) -> bool:
    return _state.mouse_down(name)


def key_down(vk: int) -> bool:
    return _state.key_down(vk)


def recent_events() -> list[str]:
    """Most recent key/button transitions, newest first."""
    return _state.recent()


def _run() -> None:
    global _state_reason
    try:
        _run_inner()
    except Exception as exc:
        _state.ok = False
        _state_reason = repr(exc)


def _run_inner() -> None:
    hinst = user32.GetModuleHandleW(None)

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    def wnd_proc(hwnd, msg, wparam, lparam):
        try:
            if msg == WM_INPUT:
                _handle_input(lparam)
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    global _proc_ref
    _proc_ref = WNDPROC(wnd_proc)

    global _state_reason
    cls = WNDCLASSEXW()
    cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
    cls.lpfnWndProc = ctypes.cast(_proc_ref, ctypes.c_void_p)
    cls.hInstance = hinst
    cls.lpszClassName = "ValAimRawSink"
    if not user32.RegisterClassExW(ctypes.byref(cls)):
        _state_reason = f"RegisterClassExW err={ctypes.get_last_error()}"
        return

    hwnd = user32.CreateWindowExW(
        0x00000080 | 0x00000004,  # TOOLWINDOW | NOACTIVATE
        "ValAimRawSink", "ValAimRawSink", 0, 0, 0, 0, 0,
        None, None, hinst, None,
    )
    if not hwnd:
        _state_reason = f"CreateWindowExW err={ctypes.get_last_error()}"
        return

    devs = (RAWINPUTDEVICE * 2)(
        RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd),  # mouse
        RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd),  # keyboard
    )
    ok = user32.RegisterRawInputDevices(
        ctypes.byref(devs), 2, ctypes.sizeof(RAWINPUTDEVICE)
    )
    if not ok:
        _state_reason = f"RegisterRawInputDevices err={ctypes.get_last_error()}"
        return

    _state.ok = True

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _handle_input(lparam):
    hraw = wintypes.HANDLE(lparam & 0xFFFFFFFFFFFFFFFF)
    size = wintypes.UINT(0)
    user32.GetRawInputData(hraw, RID_INPUT, None, ctypes.byref(size), _HEADER)
    if size.value < _HEADER + 8:
        return
    buf = ctypes.create_string_buffer(size.value)
    got = user32.GetRawInputData(hraw, RID_INPUT, buf, ctypes.byref(size), _HEADER)
    if got == 0 or got == 0xFFFFFFFF:
        return
    raw = buf.raw[: size.value]
    dw_type = int.from_bytes(raw[0:4], "little")

    if dw_type == RIM_TYPEMOUSE:
        # RAWMOUSE: usFlags(0) [pad](2) usButtonFlags(4) usButtonData(6)
        flags = int.from_bytes(raw[_HEADER + 4: _HEADER + 6], "little")
        if not flags:
            return
        with _state.lock:
            for bit, name in MOUSE_DOWN.items():
                if flags & bit and name not in _state.buttons:
                    _state.buttons.add(name)
                    _state._log(f"{name.upper()} DOWN")
            for bit, name in MOUSE_UP.items():
                if flags & bit and name in _state.buttons:
                    _state.buttons.discard(name)
                    _state._log(f"{name.upper()} UP")
    elif dw_type == RIM_TYPEKEYBOARD:
        # RAWKEYBOARD: MakeCode(0) Flags(2) Reserved(4) VKey(6) Message(8)
        kb_flags = int.from_bytes(raw[_HEADER + 2: _HEADER + 4], "little")
        vk = int.from_bytes(raw[_HEADER + 6: _HEADER + 8], "little")
        if not vk:
            return
        make = not (kb_flags & 0x03)
        with _state.lock:
            if make and vk not in _state.keys:
                _state.keys.add(vk)
                _state._log(f"KEY 0x{vk:02X} DOWN")
            elif not make and vk in _state.keys:
                _state.keys.discard(vk)
                _state._log(f"KEY 0x{vk:02X} UP")
