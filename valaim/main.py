import argparse
import json
import math
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
    p.add_argument("--model", default="models/yolo11n_valorant_head_body/model.onnx")
    p.add_argument("--model-info", default=None)
    p.add_argument("--backend", default="auto", choices=["auto", "cuda", "tensorrt", "amd", "directml", "cpu"])
    p.add_argument("--device-id", type=int, default=0)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--capture", default="center", choices=["center", "full"])
    p.add_argument("--capture-anchor", default="crosshair", choices=["crosshair", "cursor"])
    p.add_argument("--crop", type=int, default=640)
    p.add_argument("--monitor", type=int, default=0)
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--iou", type=float, default=0.45)
    p.add_argument("--classes", nargs="*", default=[])
    p.add_argument("--exclude-classes", nargs="*", default=[])
    p.add_argument("--fov", type=int, default=500)
    p.add_argument("--aim-height", type=float, default=0.30)
    p.add_argument("--aim-mode", default="head", choices=["head", "head_wide", "body"])
    p.add_argument("--head-height", type=float, default=0.10)
    p.add_argument("--head-width", type=float, default=0.16)
    p.add_argument("--move-fraction", type=float, default=0.65)
    p.add_argument("--max-step", type=int, default=120)
    p.add_argument("--key", default="", help="Hold this key to aim (default: always on)")
    p.add_argument("--fps", type=int, default=60, help="Cap the aim loop at this FPS (0 = unlimited)")
    p.add_argument("--triggerbot", action="store_true")
    p.add_argument("--trigger-radius", type=int, default=80)
    p.add_argument("--input-backend", default="auto", choices=["auto", "bt", "sendinput"])
    p.add_argument("--bt-host", default=None, help="Phone IP shown in the BtAimBridge app")
    p.add_argument("--bt-port", type=int, default=47800)
    p.add_argument("--bt-test", action="store_true", help="Ignore the model; just drive the mouse in a circle to test the BT link")
    p.add_argument("--bt-test-radius", type=int, default=60)
    p.add_argument("--debug", action="store_true")
    p.add_argument("--max-frames", type=int, default=0)
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
        iou_threshold=args.iou,
        target_classes=tuple(args.classes),
        exclude_classes=tuple(args.exclude_classes),
        fov_radius=args.fov,
        aim_mode=args.aim_mode,
        aim_height=args.aim_height,
        head_height=args.head_height,
        head_width=args.head_width,
        move_fraction=args.move_fraction,
        max_step=args.max_step,
        keybind=args.key,
        triggerbot=args.triggerbot,
        trigger_radius=args.trigger_radius,
        input_backend=args.input_backend,
        bt_host=args.bt_host,
        bt_port=args.bt_port,
        bt_test=args.bt_test,
        bt_test_radius=args.bt_test_radius,
        debug=args.debug,
        max_frames=args.max_frames,
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


def run(cfg: AimConfig) -> None:
    import cv2

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
    selector = TargetSelector(cfg)
    engine = AimEngine(
        AimParams(
            move_fraction=cfg.move_fraction,
            max_step=cfg.max_step,
            min_move=cfg.min_move,
        )
    )

    print(f"Model: {cfg.model_path}")
    print(f"Active provider: {detector.session.get_providers()}")
    print(f"Backend requested: {cfg.backend}")

    try:
        backend = set_backend(cfg.input_backend, bt_host=cfg.bt_host, bt_port=cfg.bt_port)
    except Exception as exc:
        print(f"[input] failed to initialise backend '{cfg.input_backend}': {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Input backend: {backend}")
    if backend == "bt":
        print(f"[input] BT bridge -> {cfg.bt_host}:{cfg.bt_port} (phone must be paired as Bluetooth mouse)")
    if backend == "sendinput":
        print("[input] WARNING: SendInput is dropped while Vanguard-protected games are focused.")

    if cfg.bt_test:
        bt_test_loop(cfg)
        return

    print(f"Keybind: hold {cfg.keybind} to aim (point your crosshair near the enemy)")

    if cfg.debug:
        cv2.namedWindow("valaim", cv2.WINDOW_NORMAL)

    clicking = False
    frames = 0
    log_file = log_path()
    last_log_time = 0.0
    last_status = ""
    print(f"Log: {log_file}")

    while True:
        if cfg.max_frames and frames >= cfg.max_frames:
            break

        frame_start = time.monotonic()
        img, crop_x, crop_y, crop_w, crop_h = capture.grab()
        detections = detector.detect(img, crop_x, crop_y)
        cursor = capture.crosshair()
        # Always-on by default; pass --key to gate on a held key instead.
        active = press_key(cfg.keybind) if cfg.keybind else True
        target = selector.select(detections, cursor) if active else None
        dist = None
        action = ""

        if target:
            dx = target.x - cursor[0]
            dy = target.y - cursor[1]
            dist = math.hypot(dx, dy)

            if dist > cfg.min_move:
                mx, my = engine.step(dx, dy)
                if mx or my:
                    move_mouse(mx, my)
                    action = f"move {mx:+d},{my:+d}"
                else:
                    action = "hold (deadzone)"
            else:
                action = "locked (already close)"

        if active:
            status = selector.explain(detections, cursor)
            if action:
                status = f"{status} | {action}"
        elif cfg.keybind:
            ks = key_state(cfg.keybind)
            if ks == -1:
                key_txt = "key polling disabled (not running on Windows)"
            elif ks == -2:
                key_txt = f"unknown key '{cfg.keybind}'"
            else:
                key_txt = f"{cfg.keybind} not pressed (state=0x{ks:04X})"
            status = f"idle | {key_txt} | detections={len(detections)}"
        else:
            status = f"idle | detections={len(detections)}"

        now = time.monotonic()
        if status != last_status or now - last_log_time >= 1.0:
            write_log(log_file, status)
            last_log_time = now
            last_status = status

        want_click = bool(cfg.triggerbot and active and dist is not None and dist <= cfg.trigger_radius)
        if want_click != clicking:
            set_mouse_button("left", want_click)
            clicking = want_click

        if cfg.debug:
            frame = img.copy()
            draw_debug(frame, detections, target, cursor, crop_x, crop_y, active, status)
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

    if cfg.max_frames:
        print(f"Done: {frames} frames")

    close_backend()


def main() -> None:
    args = parse_args()
    run(config_from_args(args))


if __name__ == "__main__":
    main()
