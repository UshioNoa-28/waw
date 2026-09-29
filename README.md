# ValAim

External Valorant aimbot: captures the screen around the crosshair, runs a YOLO model through ONNX Runtime, and moves the mouse via either a virtual HID device (bypasses Vanguard's synthetic-input filter) or the legacy `SendInput` path.

It supports:

- CPU: `onnxruntime`
- AMD iGPU/dGPU: `onnxruntime-directml`
- NVIDIA CUDA: `onnxruntime-gpu`
- Optional TensorRT backend for NVIDIA

## Setup

Use `uv` and create one environment per runtime backend. Do not install `onnxruntime`, `onnxruntime-gpu`, and `onnxruntime-directml` in the same environment.

```bash
uv venv --python 3.12
```

CPU:

```bash
uv sync --extra cpu
```

AMD:

```bash
uv sync --extra amd
```

NVIDIA CUDA:

```bash
uv sync --extra cuda
```

Export tooling:

```bash
uv sync --extra export
```

## Models

The default model is a **YOLO11n** detector with two classes, `enemy` and `enemy_head`, so head and body are genuinely distinguished and only enemies are targeted. It is bundled in `models/yolo11n_valorant_head_body/`.

Source: `tutoudelihua/AI_assisted_auto_aiming` (`model/yolo11n_valorant_head_body.onnx`, already ONNX, no export needed). It is based on the Roboflow dataset `brolikeshooting/valorant-object-detection2-yuw86` (classes `enemyBody`, `enemyHead`).

Other useful public models (trade-offs):

| Model | Classes | Notes |
|---|---|---|
| `yolo11n_valorant_head_body` | `enemy`, `enemy_head` | **default**, lightweight, head + body |
| `jparedesDS/valorant-yolo11m` | `Body`, `Head` | gated, heavier (67.7 GFLOPs), no team filter |
| `keremberke/yolov8s-valorant-detection` | `enemy`, `teammate`, spikes | best mAP (0.971) but **no head class** |

### Re-exporting a Ultralytics `.pt`

```bash
uv run --extra export python scripts/export_model.py \
  --repo keremberke/yolov8s-valorant-detection \
  --filename best.pt \
  --out-dir models/yolov8s_valorant
```

Gated repos need "Agree and access repository" on the model page and a token with access:

```bash
uv run --extra export python scripts/export_model.py \
  --repo jparedesDS/valorant-yolo11m \
  --filename vlr-yolo11m.pt \
  --token hf_... \
  --out-dir models/yolo11m_valorant
```

Local `.pt` file:

```bash
uv run --extra export python scripts/export_model.py \
  --local-file path/to/model.pt \
  --out-dir models/my_model
```

The script writes:

- `models/.../model.onnx`
- `models/.../model.json`

## Run

AMD iGPU example:

```bash
uv run --extra amd python -m valaim.main \
  --model models/yolo11n_valorant_head_body/model.onnx \
  --model-info models/yolo11n_valorant_head_body/model.json \
  --backend amd \
  --classes enemy enemy_head \
  --fov 250 \
  --conf 0.35 \
  --debug
```

NVIDIA CUDA example:

```bash
uv run --extra cuda python -m valaim.main \
  --model models/yolo11n_valorant_head_body/model.onnx \
  --model-info models/yolo11n_valorant_head_body/model.json \
  --backend cuda \
  --classes enemy enemy_head \
  --fov 250 \
  --debug
```

TensorRT example:

```bash
uv run --extra cuda python -m valaim.main \
  --model models/yolo11n_valorant_head_body/model.onnx \
  --backend tensorrt \
  --classes enemy enemy_head
```

CPU fallback:

```bash
uv run --extra cpu python -m valaim.main \
  --model models/yolo11n_valorant_head_body/model.onnx \
  --backend cpu
```

## Controls

- Hold `--key` (default `F1`) to enable aiming.
- Press `q` in the debug window to quit.
- `--debug` shows the capture crop, detections, crosshair, and selected aim point.

## Useful Options

```text
--capture center|full       Capture around the crosshair or full monitor
--capture-anchor crosshair  Aim reference: screen centre (crosshair) or OS cursor
--crop 640                  Square crop size around the crosshair
--fov 250                   Maximum distance from crosshair to target
--conf 0.35                 Confidence threshold
--iou 0.45                  NMS IoU threshold
--classes enemy enemy_head  Only target these class names
--exclude-classes teammate  Ignore these class names
--aim-mode head             Aim point: head, head_wide, or body
--aim-height 0.30           Aim at this fraction down the box (body mode)
--head-height 0.10          Head aim point as a fraction down the box (head mode)
--head-width 0.16           Head aim point as a fraction down the box (head_wide mode)
--move-fraction 0.65        Fraction of remaining distance moved per frame
--max-step 120              Maximum mouse movement per frame
--triggerbot                Click while the aim point is inside trigger radius
--trigger-radius 80         Triggerbot radius in pixels
--input-backend auto        auto|bt|sendinput (see Notes)
--bt-host 192.168.x.x       Phone IP shown in the BtAimBridge app
--bt-port 47800             TCP port for the Bluetooth bridge
--bt-test                   Ignore the model; draw circles to verify the BT link
--max-frames 0              Run only this many frames; 0 disables the limit
```

## Notes

- The runtime only needs ONNX Runtime, OpenCV, `mss`, and NumPy.
- Export needs PyTorch/Ultralytics only once.
- **Input backends (`--input-backend`):**
  - `bt` - sends relative moves over the LAN to the **BtAimBridge** Android app, which re-emits them as a real **Bluetooth HID mouse**. This bypasses Vanguard's synthetic-input filter (verified working while the game is focused) with **no kernel driver and no extra hardware**. Requires a phone running the app; see `android/README.md`. Set the phone IP with `--bt-host` (or the `BT_BRIDGE_HOST` env var).
  - `sendinput` - classic `SendInput`/`mouse_event`. Only works when the game is **not** focused under Vanguard.
  - `auto` (default) - uses `bt` when a host is configured, otherwise `sendinput`.
- **Aiming while the game is focused:** Valorant locks the crosshair to the centre of the monitor and hides the OS cursor, so the aim reference is the monitor centre (`--capture-anchor crosshair`, the default), not the cursor. Use `--capture-anchor cursor` only if you really want to follow the desktop pointer. Use `--input-backend bt` so input survives the game being focused.
- **Fullscreen vs borderless:** exclusive fullscreen can make `mss` return black frames. Set Valorant to **Windowed Fullscreen (Borderless)** on the same monitor as `--monitor`.
- When the model exposes a head class (`head`, `enemy_head`, `Head`, ...), that detection is aimed at directly and given a `head_boost` so it wins over the body box. When it does not, the aim point is estimated from the body box via `--aim-mode head` (top-center), `head_wide` (slightly lower), or `body` (chest).
- If a model was exported with built-in NMS, the decoder tries to handle both raw YOLO output and end-to-end detection output.
- If `--model-info` is omitted, a neighboring `model.json` file is used automatically when present.
