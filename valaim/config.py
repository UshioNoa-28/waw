from dataclasses import dataclass, field


@dataclass
class AimConfig:
    model_path: str = "models/valorant_head_body/model.onnx"
    model_info: str | None = None
    backend: str = "auto"
    device_id: int = 0
    imgsz: int = 640

    capture_mode: str = "center"
    capture_anchor: str = "crosshair"
    crop_size: int = 640
    monitor: int = 0

    conf_threshold: float = 0.35
    iou_threshold: float = 0.45
    target_classes: tuple[str, ...] = field(default_factory=tuple)
    exclude_classes: tuple[str, ...] = field(default_factory=tuple)

    fov_radius: int = 160
    aim_mode: str = "head"
    aim_height: float = 0.30
    head_height: float = 0.10
    head_width: float = 0.16
    head_bias: float = 0.55
    head_boost: float = 0.35
    min_move: int = 2
    move_fraction: float = 0.4
    max_step: int = 60
    aim_lead: float = 0.1
    aim_floor: float = 2.0
    aim_comp: int = 5
    aim_cw: float = 1.0
    smoothing: float = 0.6
    deadzone: float = 2.5
    head_offset_y: float = 0.0
    aim_gain: float = 0.0
    calibrate: bool = False
    sens: float = 0.0
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
