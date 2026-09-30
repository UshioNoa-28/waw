from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    x: float
    y: float
    w: float
    h: float
    conf: float
    cls: int
    name: str


def _letterbox(img: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / np.maximum(1e-6, areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_threshold]
    return keep


class OnnxDetector:
    def __init__(
        self,
        model_path: str,
        imgsz: int = 640,
        backend: str = "auto",
        device_id: int = 0,
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        class_names: tuple[str, ...] | None = None,
    ):
        import onnxruntime as ort

        self.model_path = model_path
        self.imgsz = imgsz
        self.backend = backend
        self.device_id = device_id
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.class_names = list(class_names or [])
        self.providers = self._providers(ort)
        self._is_cpu = "CPUExecutionProvider" == (self.providers[0] if isinstance(self.providers[0], str) else self.providers[0][0])
        self._gpu_fail = 0
        self._cpu_since = 0.0
        self.session = self._create_session(ort)
        self.input_name = self.session.get_inputs()[0].name
        # Guard: a static-shape model rejects any other imgsz at run() time
        # (that crash looked like INVALID_ARGUMENT Got:320 Expected:640).
        # Adapt imgsz to the model instead of dying.
        try:
            dims = self.session.get_inputs()[0].shape
            h, w = dims[2], dims[3]
            if isinstance(h, int) and isinstance(w, int) and (h != self.imgsz or w != self.imgsz):
                print(f"[inference] model is fixed {w}x{h}; ignoring --imgsz {self.imgsz}, using {h}")
                self.imgsz = int(h)
        except (IndexError, TypeError):
            pass
        self.output_name = self._choose_output(ort)

    def _providers(self, ort) -> list:
        available = set(ort.get_available_providers())
        cpu = "CPUExecutionProvider"

        if self.backend == "auto":
            candidates = []
            if "CUDAExecutionProvider" in available:
                candidates.append(("CUDAExecutionProvider", {"device_id": self.device_id}))
            if "DmlExecutionProvider" in available:
                candidates.append(("DmlExecutionProvider", {"device_id": self.device_id}))
            candidates.append(cpu)
            return candidates

        if self.backend == "cuda":
            if "CUDAExecutionProvider" in available:
                return [("CUDAExecutionProvider", {"device_id": self.device_id}), cpu]
            return [cpu]

        if self.backend == "tensorrt":
            if "TensorRTExecutionProvider" in available:
                return [
                    ("TensorRTExecutionProvider", {"device_id": self.device_id}),
                    ("CUDAExecutionProvider", {"device_id": self.device_id}),
                    cpu,
                ]
            return [cpu]

        if self.backend in {"amd", "directml"}:
            if "DmlExecutionProvider" in available:
                return [("DmlExecutionProvider", {"device_id": self.device_id}), cpu]
            return [cpu]

        if self.backend == "cpu":
            return [cpu]

        if self.backend in available:
            return [self.backend, cpu]

        raise ValueError(f"Unknown or unavailable ONNX Runtime backend: {self.backend}")

    def _create_session(self, ort):
        try:
            return ort.InferenceSession(self.model_path, providers=self.providers)
        except Exception:
            if self.backend == "cpu":
                raise
            sess = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            self._is_cpu = True
            self._cpu_since = 0.0
            return sess

    def _choose_output(self, ort) -> str:
        outputs = self.session.get_outputs()
        if len(outputs) == 1:
            return outputs[0].name

        shapes = []
        for out in outputs:
            dims = out.shape
            if any(dim is None or dim < 0 for dim in dims):
                shapes.append(0)
            else:
                shapes.append(int(np.prod(dims)))
        return outputs[int(np.argmax(shapes))].name

    def _class_name(self, cls: int) -> str:
        if 0 <= cls < len(self.class_names):
            return str(self.class_names[cls])
        return str(cls)

    def _decode(
        self,
        raw: np.ndarray,
        scale: float,
        pad_x: int,
        pad_y: int,
        offset_x: int,
        offset_y: int,
    ) -> list[Detection]:
        raw = np.squeeze(raw)
        if raw.ndim == 1:
            raw = raw[None, :]
        if raw.ndim != 2:
            raise ValueError(f"Unsupported ONNX output shape: {raw.shape}")

        rows, cols = raw.shape
        c = len(self.class_names)

        def _decode_raw_yolo(data: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            boxes = data[:, :4]
            cls = data[:, 4:].argmax(axis=1).astype(int)
            scores = data[:, 4:].max(axis=1)
            return boxes, cls, scores

        if cols == 6:
            boxes = raw[:, :4]
            cls = np.zeros(rows, dtype=int)
            scores = raw[:, 4]
        elif cols == 7:
            boxes = raw[:, :4]
            a, b = raw[:, 4], raw[:, 5]
            a_like_score = a.size == 0 or (a.min() >= -1e-6 and a.max() <= 1 + 1e-6)
            b_like_score = b.size == 0 or (b.min() >= -1e-6 and b.max() <= 1 + 1e-6)
            if b_like_score and not a_like_score:
                scores = b
                cls = a.astype(int)
            else:
                scores = a
                cls = b.astype(int)
        elif rows < cols and rows >= 5:
            boxes, cls, scores = _decode_raw_yolo(raw.T)
        elif cols == 4 + c and rows > cols:
            boxes, cls, scores = _decode_raw_yolo(raw)
        elif cols >= 4 and cols < rows:
            boxes, cls, scores = _decode_raw_yolo(raw.T)
        elif rows >= 4 and rows < cols:
            boxes, cls, scores = _decode_raw_yolo(raw)
        else:
            raise ValueError(f"Could not infer YOLO output layout: {raw.shape}")

        mask = scores >= self.conf_threshold
        boxes = boxes[mask]
        cls = cls[mask]
        scores = scores[mask]
        if len(boxes) == 0:
            return []

        x1 = boxes[:, 0] - boxes[:, 2] / 2.0
        y1 = boxes[:, 1] - boxes[:, 3] / 2.0
        x2 = boxes[:, 0] + boxes[:, 2] / 2.0
        y2 = boxes[:, 1] + boxes[:, 3] / 2.0
        keep = _nms(np.column_stack((x1, y1, x2, y2)), scores, self.iou_threshold)

        detections: list[Detection] = []
        for i in keep:
            left = (x1[i] - pad_x) / scale + offset_x
            top = (y1[i] - pad_y) / scale + offset_y
            w = (x2[i] - x1[i]) / scale
            h = (y2[i] - y1[i]) / scale
            detections.append(
                Detection(
                    x=float(left),
                    y=float(top),
                    w=float(w),
                    h=float(h),
                    conf=float(scores[i]),
                    cls=int(cls[i]),
                    name=self._class_name(int(cls[i])),
                )
            )
        return detections

    def _make_session(self, cpu_only: bool):
        import time as _t

        import onnxruntime as ort

        providers = ["CPUExecutionProvider"] if cpu_only else self.providers
        sess = ort.InferenceSession(self.model_path, providers=providers)
        self._is_cpu = cpu_only
        self._switched_at = _t.monotonic() if not cpu_only else self._switched_at
        return sess

    def _recover(self) -> bool:
        """Called after a runtime inference failure. Try to get back on GPU."""
        import sys
        import time as _t

        now = _t.monotonic()

        # 1) GPU session died -> rebuild the GPU session and retry
        if not self._is_cpu:
            self._gpu_fail = getattr(self, "_gpu_fail", 0) + 1
            try:
                self.session = self._make_session(cpu_only=False)
                print(f"[inference] GPU hiccup #{self._gpu_fail}: session rebuilt", file=sys.stderr)
                return True
            except Exception:
                pass
            # 2) rebuild failed -> temporary CPU so aiming never stops
            try:
                self.session = self._make_session(cpu_only=True)
                self._cpu_since = now
                print("[inference] GPU unusable -> CPU fallback (will retry GPU every 30s)",
                      file=sys.stderr)
                return True
            except Exception:
                return False

        # 3) already on CPU -> after 30s try upgrading back to GPU
        if now - getattr(self, "_cpu_since", 0.0) > 30.0:
            try:
                sess = self._make_session(cpu_only=False)
                self.session = sess
                print("[inference] back on GPU", file=sys.stderr)
                return True
            except Exception:
                self._cpu_since = now  # wait another 30s
        return False

    def detect(self, img: np.ndarray, offset_x: int = 0, offset_y: int = 0) -> list[Detection]:
        canvas, scale, pad_x, pad_y = _letterbox(img, self.imgsz)
        blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob.transpose(2, 0, 1)[None])
        try:
            outputs = self.session.run([self.output_name], {self.input_name: blob})
        except Exception:
            if not self._recover():
                raise
            outputs = self.session.run([self.output_name], {self.input_name: blob})
        return self._decode(outputs[0], scale, pad_x, pad_y, offset_x, offset_y)
