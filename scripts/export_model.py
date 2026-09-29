import argparse
import json
import shutil
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download a Hugging Face Ultralytics model and export it to ONNX.")
    p.add_argument("--repo", default="keremberke/yolov8n-valorant-detection")
    p.add_argument("--filename", default="best.pt")
    p.add_argument("--out-dir", default="models/yolov8n_valorant")
    p.add_argument("--onnx-name", default="model.onnx")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--opset", type=int, default=12)
    p.add_argument("--half", action="store_true")
    p.add_argument("--token", default=None)
    p.add_argument("--local-file", default=None, help="Use a local .pt file instead of downloading.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.local_file:
        weights_path = Path(args.local_file)
    else:
        from huggingface_hub import hf_hub_download

        weights_path = Path(
            hf_hub_download(
                repo_id=args.repo,
                filename=args.filename,
                token=args.token,
            )
        )

    from ultralytics import YOLO

    model = YOLO(str(weights_path))
    exported = Path(model.export(format="onnx", imgsz=args.imgsz, opset=args.opset, half=args.half))

    final_onnx = out_dir / args.onnx_name
    if exported.resolve() != final_onnx.resolve():
        shutil.copyfile(exported, final_onnx)

    names = model.names
    if isinstance(names, dict):
        names = [names[k] for k in sorted(names, key=lambda item: int(item))]
    else:
        names = list(names)

    metadata = {
        "model": str(final_onnx),
        "source_repo": None if args.local_file else args.repo,
        "source_file": args.filename if not args.local_file else str(args.local_file),
        "imgsz": args.imgsz,
        "opset": args.opset,
        "half": args.half,
        "layout": "raw-yolo",
        "nms": False,
        "names": names,
    }
    with open(out_dir / "model.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"Saved ONNX: {final_onnx}")
    print(f"Saved metadata: {out_dir / 'model.json'}")
    print(f"Classes: {names}")


if __name__ == "__main__":
    main()
