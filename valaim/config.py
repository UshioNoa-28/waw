from dataclasses import dataclass, field


@dataclass
class AimConfig:
    model_path: str = "models/valorant_v26s/model_fp16.onnx"
    model_info: str | None = None
    backend: str = "auto"
    device_id: int = 0
    imgsz: int = 448

    capture_mode: str = "center"
    capture_backend: str = "auto"   # auto|dxcam|dxcam-old|mss
    capture_anchor: str = "crosshair"
    crop_size: int = 640
    monitor: int = 0

    conf_threshold: float = 0.5
    team_guard: bool = False
    red_min_px: int = 6
    min_head_px: float = 16.0  # ignore targets whose head box is smaller (far = fake-prone)
    iou_threshold: float = 0.45
    target_classes: tuple[str, ...] = field(default_factory=tuple)
    exclude_classes: tuple[str, ...] = field(default_factory=tuple)

    fov_radius: int = 300
    aim_mode: str = "head"
    aim_height: float = 0.30
    head_height: float = 0.10
    head_width: float = 0.16
    head_bias: float = 0.55
    head_boost: float = 0.35
    min_move: int = 2
    move_fraction: float = 0.7
    max_step: int = 500
    aim_lead: float = 0.0
    aim_floor: float = 2.0
    arrive_px: float = 8.0
    resume_px: float = 40.0   # > measured spike p50*2: spikes must not unlatch
    med_win: int = 1          # 1 = median off
    humanize: bool = False
    react_min: float = 120.0
    react_max: float = 220.0
    ramp_s: float = 0.09
    tremor_px: float = 2.2
    tremor_hz: float = 10.0
    log_aim: bool = False
    aim_off: bool = False
    trace_path: str = "aim_trace.csv"
    start_delay: float = 3.0
    dump_dir: str = ""
    aim_comp: int = 4
    async_pipeline: bool = False  # measured SLOWER (GIL contention, no GPU gain); kept as --async experiment flag
    latch_throttle: bool = True   # half-rate detection while nailed down
    snap_dir: str = ""          # annotated frames every 5s (debug the model's eyes)
    burst: bool = True
    burst_cooldown: float = 0.28
    burst_gain: float = 1.0
    burst_min_px: float = 0.0
    settle_ms: float = 90.0
    settle_px: float = 45.0
    settle_frac: float = 1.6
    aim_cw: float = 1.0
    smoothing: float = 0.55
    deadzone: float = 4.0
    aim_dz_frac: float = 0.25
    head_offset_y: float = 0.0
    aim_gain: float = 0.0
    calibrate: bool = False
    sens: float = 0.6
    calib_file: str = ""

    keybind: str = ""
    hold_button: str = ""
    fire_button: str = ""
    fire_radius: int = 12
    triggerbot: bool = False
    trigger_radius: int = 80

    input_backend: str = "auto"
    bt_host: str | None = None
    bt_port: int = 47800
    bt_test: bool = False
    bt_test_radius: int = 60

    debug: bool = False
    frame_sleep: float = 0.001
    fps: int = 60
    max_frames: int = 0
    game_process: str = "VALORANT"
