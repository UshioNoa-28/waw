"""Windows HID host for the vendor control collection (usage page 0xFF00, usage 1).

The phone registers a BT HID mouse that ALSO exposes a vendor collection with an
8-byte input + 8-byte output report (id 1). This lets the PC push aim commands to
the phone over the *already-established Bluetooth HID link* - one hop, no router,
no WiFi/BT coexistence contention - instead of the TCP-over-WiFi socket.

Pure user-mode (SetupAPI + HID class driver), no kernel driver, no input hooks.
"""
import ctypes
import ctypes.wintypes as wt
from ctypes import (
    POINTER, addressof, byref, c_buffer, c_ubyte, c_ulong, create_string_buffer,
)
import threading

hid = ctypes.windll.hid
setupapi = ctypes.windll.setupapi
kernel32 = ctypes.windll.kernel32

GENL_BIT = 0x00000100   # DIGCF_DEVICEINTERFACE
INVALID = wt.HANDLE(-1).value

# Guid for HID class (4d1e5552-f16f-11cf-88cb-001111000030)
class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]
    def __repr__(self):
        return "%08x-%04x-%04x-%s" % (self.Data1, self.Data2, self.Data3,
                                      "".join("%02x" % b for b in self.Data4))

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT),
                ("ProductID", wt.USHORT), ("VersionNumber", wt.USHORT)]

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_size_t)]

class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("DevicePath", ctypes.c_char * 1)]

# HIDP_CAPS
class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", wt.USHORT), ("Usage", wt.USHORT),
                ("CollectionIDType", wt.ULONG)]

VENDOR_PAGE = 0xFF00
VENDOR_USAGE = 0x01
REPORT_ID = 0x01

class HidHost:
    """Opens the phone's vendor collection if present; thread-safe send, callback recv."""

    def __init__(self, vendor_id=None, product_id=None):
        self.vid = vendor_id
        self.pid = product_id
        self.path = None
        self._handle = None
        self._rx = None
        self.on_line = None          # callable(str) for phone->PC messages
        self.last_error = ""

    def find(self):
        g = GUID()
        hid.HidD_GetHidGuid(byref(g))
        hdev = setupapi.SetupDiGetClassDevsA(byref(g), None, None, GENL_BIT)
        if hdev == INVALID:
            self.last_error = "SetupDiGetClassDevs failed"
            return False
        idx = 0
        found = None
        try:
            while True:
                did = SP_DEVICE_INTERFACE_DATA()
                did.cbSize = ctypes.sizeof(did)
                if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, byref(g), idx, byref(did)):
                    break
                idx += 1
                sz = wt.DWORD(0)
                setupapi.SetupDiGetDeviceInterfaceDetailA(hdev, byref(did), None, 0, byref(sz), None)
                buf = ctypes.create_string_buffer(sz.value)
                ctypes.cast(buf, POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA)).contents.cbSize = (
                    ctypes.sizeof(wt.DWORD) + ctypes.sizeof(ctypes.c_char)) if ctypes.sizeof(wt.WPARAM) == 8 else wt.DWORD(6)
                if not setupapi.SetupDiGetDeviceInterfaceDetailA(hdev, byref(did), buf, sz.value, None, None):
                    continue
                path = ctypes.cast(buf, POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA)).contents.DevicePath
                h = kernel32.CreateFileA(path, 0, 3, None, 3, 0, None)  # read+write share, OPEN_EXISTING
                if h == INVALID:
                    continue
                try:
                    attr = HIDD_ATTRIBUTES()
                    attr.Size = ctypes.sizeof(attr)
                    if not hid.HidD_GetAttributes(h, byref(attr)):
                        continue
                    if self.vid and (attr.VendorID != self.vid or attr.ProductID != self.pid):
                        continue
                    prep = ctypes.c_void_p()
                    if not hid.HidD_GetPreparsedData(h, byref(prep)):
                        continue
                    try:
                        caps = HIDP_CAPS()
                        if hid.HidP_GetCaps(prep, byref(caps)) <= 0:
                            continue
                        if caps.UsagePage == VENDOR_PAGE and caps.Usage == VENDOR_USAGE:
                            found = path
                            break
                    finally:
                        hid.HidD_FreePreparsedData(prep)
                finally:
                    if not found:
                        kernel32.CloseHandle(h)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
        self.path = found
        return bool(found)

    def open(self, on_line=None):
        self.on_line = on_line
        if not self.path and not self.find():
            return False
        self._handle = kernel32.CreateFileA(self.path, 0xC0000000, 3, None, 3, 0, None)
        if self._handle == INVALID:
            self.last_error = "CreateFile failed %d" % kernel32.GetLastError()
            self._handle = None
            return False
        self._rx = threading.Thread(target=self._read_loop, daemon=True, name="hid-recv")
        self._rx.start()
        return True

    def close(self):
        if self._handle:
            try:
                kernel32.CancelIoEx(self._handle, None)
            except Exception:
                pass
            kernel32.CloseHandle(self._handle)
            self._handle = None

    # -- outbound: PC -> phone --
    def send_text(self, text: str) -> bool:
        """Frame ascii command as vendor output report [0x01, len, ascii...]."""
        b = text.encode("ascii", "ignore")[:6]
        pkt = bytes([REPORT_ID, 0x01, len(b)]) + b
        pkt = pkt.ljust(9, b"\x00")[:9]      # report id byte + 8 data
        return self._write(pkt)

    def ping(self, seq: int) -> bool:
        pkt = bytes([REPORT_ID, 0x41, seq & 0xFF]) + b"\x00" * 7
        return self._write(pkt[:9])

    def _write(self, pkt: bytes) -> bool:
        if not self._handle:
            return False
        buf = ctypes.create_string_buffer(pkt, len(pkt))
        ok = hid.HidD_SetOutputReport(self._handle, buf, len(pkt))
        if not ok:
            ok = kernel32.WriteFile(self._handle, buf, len(pkt), byref(wt.DWORD()), None)
        return bool(ok)

    # -- inbound: phone -> PC (input reports) --
    def _read_loop(self):
        buf = ctypes.create_string_buffer(64)
        got = wt.DWORD()
        while self._handle:
            ok = kernel32.ReadFile(self._handle, buf, 64, byref(got), None)
            if not ok or got.value == 0:
                if self._handle:
                    continue
                break
            data = buf.raw[:got.value]
            self._dispatch(data)

    def _dispatch(self, data: bytes):
        if not self.on_line:
            return
        # phone mirrors ascii lines into vendor input reports: [op=0x02, len, ascii]
        if len(data) >= 3 and data[0] == REPORT_ID and data[1] == 0x02:
            n = data[2] & 0x7F
            try:
                txt = data[3:3 + n].decode("ascii", "ignore").strip()
            except Exception:
                return
            if txt:
                self.on_line(txt)
        elif len(data) >= 2 and data[0] == REPORT_ID and data[1] == 0x41:
            self.on_line("PONGHID %d" % (data[2] if len(data) > 2 else 0))
