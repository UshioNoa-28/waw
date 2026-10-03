"""60-second experiment: which key-read channels still see input while
VALORANT/Vanguard owns the foreground?"""
import ctypes
import time


def run_probe(key: str = "f8", seconds: int = 60) -> None:
    from .hotkey import VK, start as hk_start, pop as hk_pop
    vk = VK.get(key.lower(), 0x77)
    hk_ok = hk_start(key)
    user32 = ctypes.windll.user32
    print(f"[probe] testing '{key.upper()}' for {seconds}s — switch to the game and press it 10+ times", flush=True)
    print(f"[probe] channels: RegisterHotKey={'ON' if hk_ok else 'FAIL'} | GetAsyncKeyState | GetKeyState", flush=True)
    t_end = time.time() + seconds
    n_hk = n_async = n_gs = 0
    prev_async = prev_gs = False
    while time.time() < t_end:
        if hk_pop():
            n_hk += 1
            print(f"[probe] HOTKEY hit #{n_hk}", flush=True)
        a = bool(user32.GetAsyncKeyState(vk) & 0x8000)
        g = bool(user32.GetKeyState(vk) >> 15)
        if a and not prev_async:
            n_async += 1
            print(f"[probe] ASYNC down #{n_async}", flush=True)
        prev_async = a
        if g and not prev_gs:
            n_gs += 1
            print(f"[probe] GETSTATE down #{n_gs}", flush=True)
        prev_gs = g
        time.sleep(0.002)
    print(f"[probe] SUMMARY: hotkey={n_hk} async={n_async} getkeystate={n_gs}", flush=True)
