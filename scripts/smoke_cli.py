"""Smoke test: parse every CLI combo + construct config + step the engine.
Run before packaging: python scripts/smoke_cli.py  (needs cv2/mss/numpy importable)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from valaim.main import parse_args, config_from_args
from valaim.aim_engine import AimEngine, AimParams

COMBOS = [
    [],
    ["--input-backend", "bt", "--bt-host", "127.0.0.1", "--log-aim", "--trace", "m.csv"],
    ["--no-aim", "--log-aim"],
    ["--humanize", "--arrive-px", "5", "--resume-px", "10", "--med-win", "5",
     "--aim-floor", "2", "--aim-comp", "6", "--aim-cw", "1", "--sens", "0.6",
     "--move-fraction", "0.5", "--max-step", "60", "--smoothing", "0.7",
     "--deadzone", "4", "--dz-frac", "0.35", "--fov", "160", "--key", "ALT", "--bt-test",
     "--fire-button", "x1", "--fire-radius", "12", "--head-bias", "0.7",
     "--head-offset-y", "5", "--aim-gain", "1.3", "--aim-lead", "0.12", "--fps", "120",
     "--start-delay", "5"],
    ["--hold-button", "middle", "--triggerbot", "--trigger-radius", "50", "--calibrate"],
]
for i, argv in enumerate(COMBOS):
    sys.argv = ["smoke"] + argv
    cfg = config_from_args(parse_args())
    print(f"combo {i}: ok ({cfg.input_backend}/{cfg.hold_button or '-'}{'/noaim' if cfg.aim_off else ''})")

sys.argv = ["s"] + COMBOS[1]
c = config_from_args(parse_args())
assert c.log_aim and c.trace_path == "m.csv", "trace wiring broken"
sys.argv = ["s"] + COMBOS[2]
c = config_from_args(parse_args())
assert c.aim_off, "no-aim wiring broken"
assert c.start_delay >= 0, "delay default missing"
sys.argv = ["s"] + COMBOS[3]
c = config_from_args(parse_args())
assert c.humanize and c.aim_comp == 6 and c.arrive_px == 5.0, "humanize wiring broken"
assert c.aim_dz_frac == 0.35, "dz-frac wiring broken"
assert c.start_delay == 5.0, "start-delay wiring broken"
print("wiring asserts: ok")

p = AimParams(move_fraction=0.5, arrive_px=5, resume_px=10, med_win=5,
              humanize=True, comp_frames=6, min_speed=2)
e = AimEngine(p)
e.on_new_lock()
out = e.step(120, 40, 0, 0)
assert isinstance(out, tuple) and len(out) == 2
print("engine: ok")
print("SMOKE PASS")
