"""Global hotkey trigger via RegisterHotKey (system path - works while the game
is focused, unlike raw-input reads which Vanguard suppresses)."""
import threading
import queue

VK = {
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "scrolllock": 0x91, "pause": 0x13, "apps": 0x5D, "f13": 0x7C,
}

_events: "queue.Queue[bool]" = queue.Queue()
_started = False


def start(key: str) -> bool:
    global _started
    vk = VK.get(key.lower())
    if vk is None or _started:
        return False
    _started = True

    def loop():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 0xD00D, 0x4000, vk):  # MOD_NOREPEAT
            _started_flag_clear()
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312 and msg.wParam == 0xD00D:
                try:
                    _events.put_nowait(True)
                except queue.Full:
                    pass

    def _started_flag_clear():
        global _started
        _started = False

    threading.Thread(target=loop, daemon=True, name="hotkey").start()
    return True


def pop() -> bool:
    try:
        _events.get_nowait()
        return True
    except queue.Empty:
        return False
