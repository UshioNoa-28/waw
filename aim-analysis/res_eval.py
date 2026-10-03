import glob, random
from ultralytics import YOLO
ROOT="/home/anna/code/projects/val"
files=sorted(glob.glob(f"{ROOT}/distill_data/test_frames/*.jpg")+glob.glob(f"{ROOT}/distill_data/test_frames/*.png"))
random.Random(11).shuffle(files); files=files[:150]
T=YOLO(f"{ROOT}/models/valorant_yolo11m/model.onnx")
S=YOLO(f"{ROOT}/models/valorant_v26s_r3/model.onnx")
def boxes(r): return [[float(v) for v in b.xyxy[0]] for b in r.boxes]
def iou(a,b):
    ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
    if ix<=0 or iy<=0: return 0.0
    i=ix*iy; u=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i
    return i/u if u>0 else 0.0
print("teacher@640...", flush=True)
Tb=[boxes(T.predict(f,imgsz=640,conf=0.35,device="cpu",verbose=False)[0]) for f in files]
for sz in (512,384,320):
    hit=tot=fp=th=0
    for i,f in enumerate(files):
        mb=boxes(S.predict(f,imgsz=sz,conf=0.35,device="cpu",verbose=False)[0])
        tb=Tb[i]; tot+=len(mb); th+=len(tb)
        for b_ in mb:
            if any(iou(b_,t_)>0.5 for t_ in tb): hit+=1
            else: fp+=1
    print(f"student@{sz}: 召回{100*hit/max(1,th):.0f}% 误检{100*fp/max(1,tot):.0f}% (教师框{th})", flush=True)
print("RES DONE", flush=True)
