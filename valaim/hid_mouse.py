"""User-mode client for the VHF virtual mouse driver.

The driver (`kernel/vhidmouse`) exposes:

  * a named section (``\\BaseNamedObjects\\Global\\VhidReportRing``) mapped
    into this process, and
  * a control device (``\\\\.\\Global\\VhidMouse``).

Hot-path mouse reports go through the section's lock-free ring: we bump the
``Head`` index and the driver's passive-level timer forwards the packet to
the Virtual HID Framework, which delivers it through the real HID stack.
Nothing here calls ``SendInput``/``mouse_event``, so the ``LLMHF_INJECTED``
flag never appears and the input stack sees a HID device.

If the driver is not present, :class:`VhidMouse` raises :class:`VhidError`
and the caller can fall back to :mod:`valaim.input_ctrl`.
"""

from __future__ import annotations

import ctypes
import mmap
import struct
import sys
import time

VHID_DEVICE_PATH = r"\\.\Global\VhidMouse"
VHID_SECTION_NAME = "Global\\VhidReportRing"

VHID_PROTO_VERSION = 1

VHID_RING_SLOTS = 256
VHID_RING_MASK = VHID_RING_SLOTS - 1

# sizeof(VHID_RING_HEADER): Head, Tail, Attached, Dropped, Pad
_HEADER_FMT = "<lllll"
_HEADER_SIZE = 20
_REPORT_FMT = "<Bbbb"
_REPORT_SIZE = 4
_SECTION_SIZE = _HEADER_SIZE + VHID_RING_SLOTS * _REPORT_SIZE

_FILE_DEVICE_UNKNOWN = 0x22
_METHOD_BUFFERED = 0
_FILE_ANY_ACCESS = 0
_VHID_DEVICE_TYPE = 0x8000


def _ctl_code(function: int) -> int:
    return (
        (_VHID_DEVICE_TYPE << 16)
        | (_FILE_ANY_ACCESS << 14)
        | (function << 2)
        | _METHOD_BUFFERED
    )


IOCTL_VHID_ATTACH = _ctl_code(0x800)
IOCTL_VHID_DETACH = _ctl_code(0x801)
IOCTL_VHID_QUERY = _ctl_code(0x802)

BTN_LEFT = 0x01
BTN_RIGHT = 0x02
BTN_MIDDLE = 0x04
BTN_4 = 0x08
BTN_5 = 0x10


class VhidError(RuntimeError):
    """Raised when the virtual HID driver cannot be reached."""


class VhidMouse:
    """Writes relative mouse reports into the driver's shared ring."""

    def __init__(self, attach: bool = True, max_step: int = 127) -> None:
        if sys.platform != "win32":
            raise VhidError("virtual HID input requires Windows")

        self.max_step = max(1, min(127, int(max_step)))
        self._pending_buttons = 0
        self._dropped_last = 0.0

        self._kernel32 = ctypes.windll.kernel32
        self._create_file = ctypes.windll.kernel32.CreateFileW
        self._create_file.restype = ctypes.c_void_p
        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._device_control = ctypes.windll.kernel32.DeviceIoControl
        self._device_control.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]

        self._device = self._open_device()

        try:
            self._mm = mmap.mmap(-1, _SECTION_SIZE, tagname=VHID_SECTION_NAME)
        except (OSError, ValueError) as exc:
            self._close_device()
            raise VhidError(
                f"could not open section {VHID_SECTION_NAME!r}: {exc}. "
                "Is the vhidmouse driver running?"
            ) from exc

        if attach:
            self._ioctl(IOCTL_VHID_ATTACH)

    # -- lifecycle -----------------------------------------------------

    def _open_device(self) -> ctypes.c_void_p:
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        handle = self._create_file(
            VHID_DEVICE_PATH,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle in (0, ctypes.c_void_p(-1).value, None):
            raise VhidError(
                f"could not open {VHID_DEVICE_PATH!r}. "
                "Load the vhidmouse driver first."
            )
        return ctypes.c_void_p(handle)

    def _ioctl(self, code: int, out_size: int = 0) -> bytes:
        out_buf = ctypes.create_string_buffer(max(out_size, 1))
        returned = ctypes.c_uint32(0)
        ok = self._device_control(
            self._device,
            code,
            None,
            0,
            out_buf,
            out_size,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error() or self._kernel32.GetLastError()
            raise VhidError(f"DeviceIoControl 0x{code:x} failed (error {err})")
        return out_buf.raw[: returned.value]

    def close(self) -> None:
        if getattr(self, "_mm", None) is not None:
            try:
                self._ioctl(IOCTL_VHID_DETACH)
            except VhidError:
                pass
            self._mm.close()
            self._mm = None
        self._close_device()

    def _close_device(self) -> None:
        if getattr(self, "_device", None):
            self._kernel32.CloseHandle(self._device)
            self._device = None

    def __enter__(self) -> "VhidMouse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- ring access ---------------------------------------------------

    def _read_header(self) -> tuple[int, int, int, int, int]:
        self._mm.seek(0)
        return struct.unpack(_HEADER_FMT, self._mm.read(_HEADER_SIZE))

    def _push(self, buttons: int, dx: int, dy: int, wheel: int) -> None:
        head, tail, attached, dropped, _ = self._read_header()
        if not attached:
            raise VhidError("driver reports the virtual device is not attached")

        pending = (head - tail) & 0xFFFFFFFF
        if pending >= VHID_RING_SLOTS:
            dropped += 1
            self._mm.seek(0)
            self._mm.write(struct.pack(_HEADER_FMT, head, tail, attached, dropped, 0))
            return

        slot = head & VHID_RING_MASK
        offset = _HEADER_SIZE + slot * _REPORT_SIZE
        self._mm.seek(offset)
        self._mm.write(struct.pack(_REPORT_FMT, buttons & 0xFF, dx, dy, wheel))
        self._mm.seek(0)
        self._mm.write(struct.pack(_HEADER_FMT, (head + 1) & 0xFFFFFFFF, tail, attached, dropped, 0))

    # -- public API ----------------------------------------------------

    def move(self, dx: int, dy: int) -> None:
        """Queue a relative move, splitting into <=127 HID steps."""
        if dx == 0 and dy == 0:
            return
        remaining_x, remaining_y = int(dx), int(dy)
        while remaining_x != 0 or remaining_y != 0:
            step_x = max(-self.max_step, min(self.max_step, remaining_x))
            step_y = max(-self.max_step, min(self.max_step, remaining_y))
            self._push(self._pending_buttons, step_x, step_y, 0)
            remaining_x -= step_x
            remaining_y -= step_y

    def set_buttons(self, buttons: int) -> None:
        self.button_state = buttons & 0xFF

    @property
    def button_state(self) -> int:
        return self._pending_buttons

    @button_state.setter
    def button_state(self, buttons: int) -> None:
        buttons &= 0xFF
        if buttons != self._pending_buttons:
            self._pending_buttons = buttons
            self._push(self._pending_buttons, 0, 0, 0)

    def click(self, button: str = "left", hold_ms: int = 30) -> None:
        bit = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}[button.lower()]
        self._pending_buttons |= bit
        self._push(self._pending_buttons, 0, 0, 0)
        time.sleep(hold_ms / 1000.0)
        self._pending_buttons &= ~bit
        self._push(self._pending_buttons, 0, 0, 0)

    def wheel(self, clicks: int) -> None:
        direction = 1 if clicks > 0 else -1
        for _ in range(abs(int(clicks))):
            self._push(self._pending_buttons, 0, 0, direction)

    def query(self) -> dict:
        raw = self._ioctl(IOCTL_VHID_QUERY, 16)
        version, attached, dropped, pending = struct.unpack("<LLll", raw[:16])
        return {
            "protocol_version": version,
            "attached": bool(attached),
            "dropped": dropped,
            "pending": pending,
        }


def is_available() -> bool:
    """Cheap probe: can we open the control device?"""
    if sys.platform != "win32":
        return False
    try:
        km = ctypes.windll.kernel32
        km.CreateFileW.restype = ctypes.c_void_p
        handle = km.CreateFileW(
            VHID_DEVICE_PATH, 0x80000000, 0, None, 3, 0, None
        )
        if handle in (0, ctypes.c_void_p(-1).value, None):
            return False
        km.CloseHandle(ctypes.c_void_p(handle))
        return True
    except Exception:
        return False
