import argparse
import json
import math
import random
import os
import sys
import time
from pathlib import Path

from .aim import TargetSelector
from .aim_engine import AimEngine, AimParams
from .backend import OnnxDetector
from .capture import ScreenCapture
from .config import AimConfig
from .input_ctrl import (
    foreground_process,
    mouse_button_down,
    bt_lock_events,
    active_backend,
    close_backend,
    key_state,
    move_mouse,
    press_key,
    set_backend,
    set_mouse_button,
)
from .resources import resolve_model_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="External Valorant aimbot")
    p.add_argument("--model", default="models/valorant_v26s/model.onnx")
    p.add_argument("--model-info", default=None)
    p.add_argument("--backend", default="auto", choices=["auto", "cuda", "tensorrt", "amd", "directml", "cpu"])
    p.add_argument("--device-id", type=int, default=0)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--capture", default="center", choices=["center", "full"])
    p.add_argument("--capture-anchor", default="crosshair", choices=["crosshair", "cursor"])
    p.add_argument("--crop", type=int, default=640)
    p.add_argument("--monitor", type=int, default=0)
    p.add_argument("--min-head", type=float, default=14.0, help="Ignore targets with head-box narrower than this (px)")
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--iou", type=float, default=0.45)
    p.add_argument("--classes", nargs="*", default=[])
    p.add_argument("--exclude-classes", nargs="*", default=[])
    p.add_argument("--fov", type=int, default=300)
    p.add_argument("--aim-height", type=float, default=0.30)
    p.add_argument("--aim-mode", default="head", choices=["head", "head_wide", "body"])
    p.add_argument("--head-height", type=float, default=0.10)
    p.add_argument("--head-width", type=float, default=0.16)
    p.add_argument("--head-bias", type=float, default=0.55,
                   help="Vertical aim point inside a head box (0=top, 1=bottom; default 0.55)")
    p.add_argument("--head-offset-y", type=float, default=0.0,
                   help="Extra downward aim offset in capture pixels (use if it aims too high)")
    p.add_argument("--move-fraction", type=float, default=0.7)
    p.add_argument("--max-step", type=int, default=500, help="Max counts moved per frame (slew limit)")
    p.add_argument("--aim-lead", type=float, default=0.0,
                   help="Target-motion prediction in seconds (covers actuation latency)")
    p.add_argument("--smoothing", type=float, default=0.55,
                   help="Error smoothing 0..1 (lower = steadier, more lag)")
    p.add_argument("--deadzone", type=float, default=4.0, help="Fixed stop radius in pixels (used when --dz-frac 0)")
    p.add_argument("--dz-frac", type=float, default=0.25, help="Deadzone as fraction of head-box width (0=fixed --deadzone)")
    p.add_argument("--no-async", dest="async_pipeline", action="store_false", help="Disable async perception pipeline (serial capture->infer->aim)")
    p.add_argument("--no-latch-throttle", dest="latch_throttle", action="store_false", help="Disable half-rate detection while latched")
    p.add_argument("--no-burst", dest="burst", action="store_false", help="Disable one-stroke burst mode (revert to per-frame loop)")
    p.add_argument("--burst-cooldown", type=float, default=0.28, help="Silence after a burst stroke (s)")
    p.add_argument("--burst-gain", type=float, default=1.0, help="Fraction of error per stroke")
    p.add_argument("--burst-min-px", type=float, default=0.0, help="(legacy) smallest error that triggers a stroke")
    p.add_argument("--settle-ms", type=float, default=90.0, help="Wait after a lock before firing (hand-landing gate)")
    p.add_argument("--settle-px", type=float, default=45.0, help="Absolute settle threshold (used when head box width unavailable)")
    p.add_argument("--settle-frac", type=float, default=1.6, help="Settle threshold as multiple of head-box width")
    p.add_argument("--aim-gain", type=float, default=0.0,
                   help="Mouse counts per screen pixel (0 = auto from calib file / --sens)")
    p.add_argument("--sens", type=float, default=0.6,
                   help="In-game sensitivity; compute gain from the Valorant formula (0 = use calib file)")
    p.add_argument("--calib-file", default="", help="Calibration json path (default valaim_calib.json)")
    p.add_argument("--calibrate", action="store_true", help="Runtime probe calibration (last resort)")
    p.add_argument("--calibrate-tool", action="store_true", help="Run offline gain calibration and exit")
    p.add_argument("--calib-countdown", type=float, default=8.0,
                   help="Seconds to switch back to the game before calibration starts")
    p.add_argument("--key", default="none", help="Hold this key to aim (default: none = always on)")
    p.add_argument("--hold-button", default="none",
                   choices=["none", "left", "right", "middle", "x1", "x2"],
                   help="Aim only while this mouse button is held (default: left)")
    p.add_argument("--fps", type=int, default=60, help="Cap the aim loop at this FPS (0 = unlimited)")
    p.add_argument("--arrive-px", type=float, default=8.0, help="Lock output when error inside this (px); 0=off")
    p.add_argument("--resume-px", type=float, default=32.0, help="Unlock output when error exceeds this (px)")
    p.add_argument("--med-win", type=int, default=1, help="Median filter width on detection error (1=off)")
    p.add_argument("--humanize", action="store_true", help="Reaction gate + ramp-in + tremor (human-like onset)")
    p.add_argument("--aim-floor", type=float, default=2.0, help="Min counts per frame outside deadzone (0=off)")
    p.add_argument("--aim-comp", type=int, default=4, help="In-flight compensation window, loop frames (0=off)")
    p.add_argument("--aim-cw", type=float, default=1.0, help="In-flight compensation weight 0..1")
    p.add_argument("--log-aim", action="store_true", help="Record per-frame error+commands to aim_trace.csv")
    p.add_argument("--no-aim", action="store_true", help="Record only: never move the mouse (captures human flicks in-game)")
    p.add_argument("--trace", default="aim_trace.csv", help="Trace csv path for --log-aim")
    p.add_argument("--dump-frames", default="", help="Directory: save each captured frame every ~0.5s (distillation dataset)")
    p.add_argument("--start-delay", type=float, default=3.0,
                   help="Seconds before aiming/recording/calibration starts (time to switch to the game)")
    p.add_argument("--triggerbot", action="store_true")
    p.add_argument("--trigger-radius", type=int, default=80)
    p.add_argument("--fire-button", default="none",
                   choices=["none", "x1", "x2", "middle", "right"],
                   help="Aim-fire mode: hold this physical button; the tool snaps and fires a virtual left click when locked")
    p.add_argument("--fire-radius", type=int, default=12, help="Lock radius (px) for --fire-button mode")
    p.add_argument("--input-backend", default="auto", choices=["auto", "bt", "sendinput"])
    p.add_argument("--bt-host", default=None, help="Phone IP shown in the BtAimBridge app")
    p.add_argument("--bt-port", type=int, default=47800)
    p.add_argument("--input-probe", action="store_true", help="List live key/mouse events for 30s to find what buttons actually send, then exit")
    p.add_argument("--bt-test", action="store_true", help="Ignore the model; just drive the mouse in a circle to test the BT link")
    p.add_argument("--bt-test-radius", type=int, default=60)
    p.add_argument("--debug", action="store_true")
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--game-process", default="VALORANT",
                   help="Only act while a foreground process with this name runs ('' disables the gate)")
    return p.parse_args()


def config_from_args(args: argparse.Namespace) -> AimConfig:
    return AimConfig(
        model_path=resolve_model_path(args.model),
        model_info=resolve_model_path(args.model_info) if args.model_info else None,
        backend=args.backend,
        device_id=args.device_id,
        imgsz=args.imgsz,
        capture_mode=args.capture,
        capture_anchor=args.capture_anchor,
        crop_size=args.crop,
        monitor=args.monitor,
        conf_threshold=args.conf,
        min_head_px=args.min_head,
        iou_threshold=args.iou,
        target_classes=tuple(args.classes),
        exclude_classes=tuple(args.exclude_classes),
        fov_radius=args.fov,
        aim_mode=args.aim_mode,
        aim_height=args.aim_height,
        head_height=args.head_height,
        head_bias=args.head_bias,
        head_offset_y=args.head_offset_y,
        aim_lead=args.aim_lead,
        aim_floor=args.aim_floor,
        aim_comp=args.aim_comp,
        aim_cw=args.aim_cw,
        smoothing=args.smoothing,
        deadzone=args.deadzone,
        aim_dz_frac=args.dz_frac,
        latch_throttle=args.latch_throttle,
        async_pipeline=args.async_pipeline,
        burst=args.burst,
        burst_cooldown=args.burst_cooldown,
        burst_gain=args.burst_gain,
        burst_min_px=args.burst_min_px,
        settle_ms=args.settle_ms,
        settle_px=args.settle_px,
        settle_frac=args.settle_frac,
        aim_gain=args.aim_gain,
        calibrate=args.calibrate,
        sens=args.sens,
        calib_file=args.calib_file,
        head_width=args.head_width,
        move_fraction=args.move_fraction,
        max_step=args.max_step,
        keybind="" if args.key.lower() == "none" else args.key,
        hold_button="" if args.hold_button == "none" else args.hold_button,
        triggerbot=args.triggerbot,
        trigger_radius=args.trigger_radius,
        fire_button="" if args.fire_button == "none" else args.fire_button,
        fire_radius=args.fire_radius,
        input_backend=args.input_backend,
        bt_host=args.bt_host,
        bt_port=args.bt_port,
        bt_test=args.bt_test,
        bt_test_radius=args.bt_test_radius,
        debug=args.debug,
        max_frames=args.max_frames,
        game_process=args.game_process.strip(),
        log_aim=args.log_aim,
        aim_off=args.no_aim,
        trace_path=args.trace,
        dump_dir=args.dump_frames,
        start_delay=args.start_delay,
        arrive_px=args.arrive_px,
        resume_px=args.resume_px,
        med_win=args.med_win,
        humanize=args.humanize,
    )


def load_class_names(path: str | None) -> tuple[str, ...] | None:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    names = data.get("names")
    if names is None:
        return None
    if isinstance(names, dict):
        return tuple(str(names[k]) for k in sorted(names, key=lambda item: int(item)))
    return tuple(str(name) for name in names)


def resolve_model_info(cfg: AimConfig) -> str | None:
    if cfg.model_info:
        return cfg.model_info
    sibling = Path(cfg.model_path).with_name("model.json")
    return str(sibling) if sibling.exists() else None


def draw_debug(img, detections, target, cursor, crop_x, crop_y, active: bool, status: str) -> None:
    import cv2

    for det in detections:
        x = int(det.x - crop_x)
        y = int(det.y - crop_y)
        w = int(det.w)
        h = int(det.h)
        color = (0, 255, 0) if target and target.det is det else (0, 160, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 1)
        cv2.putText(img, f"{det.name:.2s} {det.conf:.2f}", (x, max(0, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    if target:
        tx = int(target.x - crop_x)
        ty = int(target.y - crop_y)
        cv2.circle(img, (tx, ty), 2, (0, 0, 255), -1)
        cv2.line(img, (tx - 5, ty), (tx + 5, ty), (0, 0, 255), 1)
        cv2.line(img, (tx, ty - 5), (tx, ty + 5), (0, 0, 255), 1)

    cx = int(cursor[0] - crop_x)
    cy = int(cursor[1] - crop_y)
    cv2.line(img, (cx - 6, cy), (cx + 6, cy), (255, 255, 255), 1)
    cv2.line(img, (cx, cy - 6), (cx, cy + 6), (255, 255, 255), 1)
    cv2.putText(img, "aim" if active else "idle", (8, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.putText(img, status, (8, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)


def log_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.getcwd()
    return os.path.join(base, "valaim.log")


def write_log(path: str, line: str) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
    except OSError:
        pass



def input_probe_loop() -> None:
    """30s live event dump: raw-input transitions + async VK scan diff.

    Run this, then press every button you plan to bind. Tells you what the
    OS actually receives (vendor mice often remap side buttons to keys).
    """
    import ctypes
    from . import rawinput
    ok = rawinput.start()
    reason = "" if ok else rawinput.failure_reason()
    print(f"[probe] raw input sink: {'ACTIVE' if ok else 'OFF (' + reason + ')'}")
    print("[probe] 30s window - press your side buttons / keys now. Ctrl+C to stop.")
    user32 = ctypes.windll.user32
    seen_events: set[str] = set()
    down_vks: set[int] = set()
    end = time.time() + 30
    names = ("left", "right", "middle", "x1", "x2")
    while time.time() < end:
        for ev in rawinput.recent_events():
            if ev not in seen_events:
                seen_events.add(ev)
                print(f"[probe] {ev}")
        cur = {vk for vk in range(1, 256) if user32.GetAsyncKeyState(vk) & 0x8000}
        for vk in sorted(cur - down_vks):
            print(f"[probe] ASYNC KEY DOWN vk=0x{vk:02X}")
        for vk in sorted(down_vks - cur):
            print(f"[probe] ASYNC KEY UP   vk=0x{vk:02X}")
        down_vks = cur
        states = " ".join(f"{n}={int(rawinput.mouse_button_down(n))}" for n in names)
        print(f"[probe] live {states}", flush=True)
        time.sleep(0.1)
    print("[probe] done")


def bt_test_loop(cfg: AimConfig) -> None:
    """Drive the mouse in a circle forever, to verify the BT HID link.

    No model, no capture, no focus logic: just relative moves. If the in-game
    crosshair traces a circle, the phone->game input path works.
    """
    import math as _math

    print("BT TEST MODE: drawing circles. Move = should move in-game. Ctrl+C to stop.")
    r = max(1, cfg.bt_test_radius)
    step_hz = 120.0
    prev_x = float(r)
    prev_y = 0.0
    angle = 0.0
    t = 0.0
    while True:
        angle += 2.0 * _math.pi / 60.0  # full circle every 60 steps
        x = r * _math.cos(angle)
        y = r * _math.sin(angle)
        dx = int(round(x - prev_x))
        dy = int(round(y - prev_y))
        prev_x, prev_y = x, y
        if dx or dy:
            move_mouse(dx, dy)
        time.sleep(1.0 / step_hz)


def calibrate_counts_per_px(capture: ScreenCapture, _status) -> float | None:
    """Measure how many screen pixels one mouse count moves the view.

    Sends one probe move, compares two frames by phase correlation, and
    restores the view. Returns counts-per-pixel (what the engine needs).
    """
    from .calibrate import measure_shift

    probe = 64
    _status("校准鼠标增益:发送一次试探移动...")
    img1, *_ = capture.grab()
    move_mouse(probe, 0)
    time.sleep(0.22)
    img2, *_ = capture.grab()
    move_mouse(-probe, 0)
    time.sleep(0.05)

    res = measure_shift(img1, img2)
    if res is None or res[1] < 0.02:
        return None
    px = abs(res[0])
    if px < 2.0:
        return None
    return probe / px


def run(cfg: AimConfig, stop_flag=None, status=None) -> None:
    import cv2

    def _status(text: str) -> None:
        if status is not None:
            try:
                status.set(text)
            except Exception:
                pass

    model_info = resolve_model_info(cfg)
    names = load_class_names(model_info)
    detector = OnnxDetector(
        model_path=cfg.model_path,
        imgsz=cfg.imgsz,
        backend=cfg.backend,
        device_id=cfg.device_id,
        conf_threshold=cfg.conf_threshold,
        iou_threshold=cfg.iou_threshold,
        class_names=names,
    )
    capture = ScreenCapture(
        monitor=cfg.monitor,
        mode=cfg.capture_mode,
        crop_size=cfg.crop_size,
        anchor=cfg.capture_anchor,
    )
    perceptor = None
    if cfg.async_pipeline:
        from .perceptor import Perceptor

        def _cap_factory():
            return ScreenCapture(
                monitor=cfg.monitor,
                mode=cfg.capture_mode,
                crop_size=cfg.crop_size,
                anchor=cfg.capture_anchor,
            )

        _pr = Perceptor(_cap_factory, detector)
        if _pr.start():
            perceptor = _pr
            print("[perf] async pipeline ON (worker: capture+inference @ max rate)")
        else:
            print("[perf] async worker failed to start; serial fallback")

    selector = TargetSelector(cfg)
    engine = AimEngine(
        AimParams(
            move_fraction=cfg.move_fraction,
            max_step=cfg.max_step,
            min_move=cfg.min_move,
            smoothing=cfg.smoothing,
            deadzone=cfg.deadzone,
            dz_frac=cfg.aim_dz_frac,
            burst=cfg.burst,
            burst_cooldown=cfg.burst_cooldown,
            burst_gain=cfg.burst_gain,
            burst_min_px=cfg.burst_min_px,
            settle_ms=cfg.settle_ms,
            settle_px=cfg.settle_px,
            settle_frac=cfg.settle_frac,
            arrive_px=cfg.arrive_px,
            resume_px=cfg.resume_px,
            med_win=max(1, cfg.med_win),
            humanize=cfg.humanize,
            react_ms=(cfg.react_min, cfg.react_max),
            ramp_s=cfg.ramp_s,
            tremor_px=cfg.tremor_px,
            tremor_hz=cfg.tremor_hz,
            min_speed=cfg.aim_floor,
            comp_frames=cfg.aim_comp,
            comp_weight=cfg.aim_cw,
        )
    )

    print(f"model: {os.path.basename(cfg.model_path)} | provider: {', '.join(detector.session.get_providers())}")

    if cfg.aim_off:
        backend = "off"
        print("[input] --no-aim: recording only, mouse output disabled")
    else:
        try:
            backend = set_backend(cfg.input_backend, bt_host=cfg.bt_host, bt_port=cfg.bt_port)
        except Exception as exc:
            print(f"[input] failed to initialise backend '{cfg.input_backend}': {exc}", file=sys.stderr)
            raise SystemExit(1)
    print(f"Input backend: {backend}")
    if backend == "bt":
        print(f"[input] bt -> {cfg.bt_host}:{cfg.bt_port}")
    if backend == "sendinput":
        print("[input] WARNING: SendInput is dropped while Vanguard-protected games are focused.")

    if cfg.start_delay and cfg.start_delay > 0:
        print(f"starting in {cfg.start_delay:.0f}s - switch to the game now...")
        _end = time.monotonic() + cfg.start_delay
        while time.monotonic() < _end:
            time.sleep(0.15)
        print("GO")

    # Gain (mouse counts per screen pixel) resolution order:
    #   manual --aim-gain > offline calib file > --sens formula > --calibrate probe > 1.0
    cal_gain: float | None = None
    if cfg.aim_gain > 0:
        cal_gain = cfg.aim_gain
        print(f"Aim gain: manual {cal_gain:.3f} counts/px")
        _status(f"瞄准增益(手动): {cal_gain:.2f} 计数/像素")
    else:
        from .calibrate import calib_path, load_calib, counts_per_px_formula

        data = load_calib(calib_path(cfg.calib_file))
        if data:
            cal_gain = float(data["counts_per_px"])
            print(f"Aim gain: calib file {cal_gain:.3f} counts/px ({data.get('time', '')})")
            _status(f"增益: 标定文件 {cal_gain:.2f} 计数/像素")
        elif cfg.sens > 0:
            mon = capture.monitors[capture.monitor + 1] if capture.monitor >= 0 else capture.monitors[0]
            cal_gain = counts_per_px_formula(int(mon["width"]), cfg.sens)
            print(f"Aim gain: formula sens={cfg.sens} width={mon['width']} -> {cal_gain:.3f} counts/px")
            _status(f"增益: 公式(sens {cfg.sens}) {cal_gain:.2f} 计数/像素")
        elif cfg.calibrate and backend == "bt":
            try:
                cal_gain = calibrate_counts_per_px(capture, _status)
            except Exception:
                cal_gain = None
            if cal_gain:
                print(f"Aim gain: probe {cal_gain:.3f} counts/px")
            else:
                print("[calib] probe failed, gain=1.0; run --calibrate-tool once for a fixed value")
        if not cal_gain:
            _status(f"增益: {cal_gain or 1.0:.2f} 计数/像素(未标定)")

    if cfg.bt_test:
        bt_test_loop(cfg)
        return

    if cfg.debug:
        cv2.namedWindow("valaim", cv2.WINDOW_NORMAL)

    clicking = False
    shooting = False
    frames = 0
    vel: dict = {}
    lock_on = True
    tick = 0
    det_cache = None
    _dump_t = 0.0
    _dump_next = 0.0  # phone [锁定] button toggles (Vanguard hides all local keys in game)
    trace = None
    trace_f = None
    if cfg.log_aim:
        import csv as _csv
        t0 = time.monotonic()
        trace_f = open(cfg.trace_path, "w", newline="", encoding="utf-8")
        trace = _csv.writer(trace_f)
        trace.writerow(["kind", "t", "a", "b"])
        trace.writerow(["meta", 0, cfg.aim_gain or cal_gain or 1.0, cfg.fps])
        # row kinds: meta,cp,fps | err,cursor->target px | det,x,y,w,h,conf,cls | cmd,dx,dy
    log_file = log_path()
    last_log_time = 0.0
    last_status = ""
    while True:
        if stop_flag is not None and stop_flag.is_set():
            break
        if cfg.max_frames and frames >= cfg.max_frames:
            break

        frame_start = time.monotonic()

        # Push live-tunable params (the GUI can change cfg at any time).
        engine.p.move_fraction = cfg.move_fraction
        engine.p.max_step = cfg.max_step
        engine.p.min_move = cfg.min_move
        engine.p.smoothing = cfg.smoothing
        engine.p.deadzone = cfg.deadzone
        engine.p.dz_frac = cfg.aim_dz_frac
        engine.p.min_speed = cfg.aim_floor
        if cfg.imgsz and getattr(detector, "imgsz", None) and cfg.imgsz != detector.imgsz:
            detector.imgsz = cfg.imgsz      # model is dynamic-shape: hot resize
        if capture.crop_size != cfg.crop_size:
            capture.crop_size = cfg.crop_size  # hot resize capture square
        engine.p.burst = cfg.burst
        engine.p.burst_cooldown = cfg.burst_cooldown
        engine.p.burst_gain = cfg.burst_gain
        engine.p.settle_ms = cfg.settle_ms
        engine.p.settle_frac = cfg.settle_frac
        engine.p.comp_frames = cfg.aim_comp
        engine.p.comp_weight = max(0.0, min(1.0, cfg.aim_cw))
        engine.p.counts_per_px = cfg.aim_gain if cfg.aim_gain > 0 else (cal_gain or 1.0)
        engine.p.arrive_px = cfg.arrive_px
        engine.p.resume_px = cfg.resume_px
        engine.p.med_win = max(1, cfg.med_win)
        engine.p.humanize = cfg.humanize
        engine.p.min_speed = cfg.aim_floor
        engine.p.comp_frames = cfg.aim_comp
        engine.p.comp_weight = max(0.0, min(1.0, cfg.aim_cw))
        if perceptor is not None:
            snap = perceptor.pop_new(0.1)
            if snap is None:
                fl0 = bt_lock_events()
                if fl0:
                    lock_on = not lock_on
                time.sleep(0.002)
                continue
            img, crop_x, crop_y, crop_w, crop_h = snap.img, snap.crop_x, snap.crop_y, snap.crop_w, snap.crop_h
            detections, cursor = snap.detections, snap.cursor
            tick += 1
        else:
            if cfg.latch_throttle and engine.is_latched and det_cache is not None and (tick % 2 == 0):
                img, crop_x, crop_y, crop_w, crop_h, detections = det_cache
            else:
                img, crop_x, crop_y, crop_w, crop_h = capture.grab()
                detections = detector.detect(img, crop_x, crop_y)
                det_cache = (img, crop_x, crop_y, crop_w, crop_h, detections)
            tick += 1
            cursor = capture.crosshair()
        if cfg.dump_dir and time.monotonic() - _dump_t >= _dump_next:
            _dump_t = time.monotonic()
            _dump_next = random.uniform(0.4, 1.2)
            try:
                os.makedirs(cfg.dump_dir, exist_ok=True)
                import cv2 as _cv
                _fp = os.path.join(cfg.dump_dir, f"f{int(time.time() * 10)}_{tick:05d}.jpg")
                _cv.imwrite(_fp, img, [_cv.IMWRITE_JPEG_QUALITY, 92])
            except OSError:
                pass
        # Trigger selection. fire_button mode: aim while the button is held and
        # the virtual left click is emitted once locked.
        if cfg.fire_button:
            active = mouse_button_down(cfg.fire_button)
        elif cfg.keybind:
            active = press_key(cfg.keybind)
        elif cfg.hold_button:
            active = mouse_button_down(cfg.hold_button)
        else:
            active = True
        # Foreground gate: never move the mouse unless the game owns focus.
        in_game = True
        gate_note = ""
        if cfg.game_process:
            fpname = foreground_process()
            in_game = bool(fpname) and cfg.game_process.upper() in fpname.upper()
            if not in_game:
                gate_note = f"游戏未在前台(当前:{fpname or '未知'})"
        active = active and in_game
        flips = bt_lock_events()
        if flips:
            lock_on = not lock_on if flips % 2 else lock_on
        if not lock_on:
            active = False
            gate_note = "锁定OFF - 手机按[锁定]恢复"
        target = selector.select(detections, cursor) if active else None
        dist = None
        action = ""

        if target:
            dx = target.x - cursor[0]
            dy = target.y - cursor[1]
            dist = math.hypot(dx, dy)

            # Target screen-velocity estimate (px/s) for lead compensation.
            # Continuity = center distance (Detection objects are recreated
            # every frame, so id() never matches).
            nowt = time.monotonic()
            same = (vel.get("t", 0.0) and nowt - vel["t"] < 0.4
                    and math.hypot(target.x - vel["x"], target.y - vel["y"]) < 30)
            if not same:
                vel.update(x=target.x, y=target.y, t=nowt, vx=0.0, vy=0.0)
                engine.on_new_lock()
            else:
                ddt = max(0.008, nowt - vel["t"])
                dxj = target.x - vel["x"]
                dyj = target.y - vel["y"]
                if math.hypot(dxj, dyj) > 12 and ddt < 0.1:
                    pass  # single-frame point jump = detection noise, keep velocity
                else:
                    raw_vx = dxj / ddt
                    raw_vy = dyj / ddt
                    vel["vx"] = 0.7 * vel["vx"] + 0.3 * raw_vx
                    vel["vy"] = 0.7 * vel["vy"] + 0.3 * raw_vy
                    vel.update(x=target.x, y=target.y, t=nowt)
            lead = max(0.0, min(0.3, cfg.aim_lead))
            lx, ly = vel["vx"] * lead, vel["vy"] * lead

            if dist > cfg.min_move or trace is not None:
                dw = target.det.w * (1.0 if "head" in target.det.name.lower() else 0.45)
                mx, my = engine.step(dx, dy, lx, ly, dw) if dist > cfg.min_move else (0, 0)
                if cfg.aim_off:
                    mx = my = 0
                if trace is not None:
                    tt = round(time.monotonic() - t0, 4)
                    trace.writerow(["err", tt, round(dx, 2), round(dy, 2)])
                    dbg = getattr(engine, "_dbg", None)
                    if dbg:
                        trace.writerow(["dbg", tt, round(dbg[0], 1), round(dbg[2], 1),
                                        round(dbg[1], 1), round(dbg[3], 1)])
                    for d in detections:
                        trace.writerow(["det", tt, round(d.x, 2), round(d.y, 2),
                                        round(d.w, 2), round(d.h, 2), round(d.conf, 3), d.name])
                    if mx or my:
                        trace.writerow(["cmd", tt, mx, my])
                if mx or my:
                    move_mouse(mx, my)
                    action = f"move {mx:+d},{my:+d}"
                else:
                    action = "hold (deadzone)"
            else:
                action = "locked (already close)"

        # Aim-fire mode: hold the physical fire button, and once the crosshair
        # is locked on the target, the phone emits a virtual left click (hold
        # = full auto while tracking, release = stop). Every shot, including
        # the first, is fired only after the aim is on target.
        if cfg.fire_button:
            want_fire = bool(active and dist is not None and dist <= cfg.fire_radius)
            if want_fire != shooting:
                set_mouse_button("left", want_fire)
                shooting = want_fire
            action = (action + " | FIRE" if shooting else action)

        if active:
            status_text = selector.explain(detections, cursor)
            if action:
                status_text = f"{status_text} | {action}"
        elif cfg.fire_button:
            status_text = f"idle | hold {cfg.fire_button.upper()} to aim+fire | detections={len(detections)}"
        elif cfg.hold_button:
            status_text = f"idle | hold {cfg.hold_button} mouse to aim | detections={len(detections)}"
        elif cfg.keybind:
            ks = key_state(cfg.keybind)
            if ks == -1:
                key_txt = "key polling disabled (not running on Windows)"
            elif ks == -2:
                key_txt = f"unknown key '{cfg.keybind}'"
            else:
                key_txt = f"{cfg.keybind} not pressed (state=0x{ks:04X})"
            status_text = f"idle | {key_txt} | detections={len(detections)}"
        else:
            status_text = f"idle | detections={len(detections)}"
        if not active and gate_note:
            status_text = f"idle | {gate_note}"

        now = time.monotonic()
        if status_text != last_status or now - last_log_time >= 1.0:
            write_log(log_file, status_text)
            _status(status_text)
            last_log_time = now
            last_status = status_text

        want_click = bool(cfg.triggerbot and not cfg.fire_button and active and dist is not None and dist <= cfg.trigger_radius)
        if want_click != clicking:
            set_mouse_button("left", want_click)
            clicking = want_click

        if cfg.debug:
            frame = img.copy()
            draw_debug(frame, detections, target, cursor, crop_x, crop_y, active, status_text)
            cv2.imshow("valaim", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        frames += 1
        if cfg.fps > 0:
            elapsed = time.monotonic() - frame_start
            target_dt = 1.0 / cfg.fps
            if elapsed < target_dt:
                time.sleep(target_dt - elapsed)
        else:
            time.sleep(cfg.frame_sleep)

    if shooting:
        set_mouse_button("left", False)
    if cfg.max_frames:
        print(f"Done: {frames} frames")

    close_backend()


def main() -> None:
    args = parse_args()
    cfg = config_from_args(args)
    if args.input_probe:
        input_probe_loop()
        raise SystemExit(0)
    if args.calibrate_tool:
        from .calibrate import run_calibration

        raise SystemExit(run_calibration(cfg, [16, 32, 64, 96, 128], reps=3, settle=0.25, countdown=args.calib_countdown))
    run(cfg)


if __name__ == "__main__":
    main()
