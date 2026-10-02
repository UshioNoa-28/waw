import glob, random, math
from ultralytics import YOLO
ROOT="/home/anna/code/projects/val"
files=sorted(glob.glob(f"{ROOT}/distill_data/test_frames/*.jpg")+glob.glob(f"{ROOT}/distill_data/test_frames/*.png"))
random.Random(11).shuffle(files); files=files[:120]
T=YOLO(f"{ROOT}/models/valorant_yolo11m/model.onnx")
S=YOLO(f"{ROOT}/models/valorant_v26s/model.onnx")
def bx(r): return [[float(v) for v in b.xyxy[0]] for b in r.boxes]
def iou(a,b):
    ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
    if ix<=0 or iy<=0: return 0.0
    i=ix*iy; u=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i
    return i/u if u>0 else 0.0
buckets={"<20px":[0,0],"20-40":[0,0],"40-80":[0,0],">80":[0,0]}
def bk(w): return "<20px" if w<20 else "20-40" if w<40 else "40-80" if w<80 else ">80"
# teacher @1280 (better small-head ground truth)
print("teacher@1280 ...", flush=True)
Tb=[bx(T.predict(f,imgsz=1280,conf=0.28,device="cpu",verbose=False)[0]) for f in files]
print("student@640 ...", flush=True)
Sb=[bx(S.predict(f,imgsz=640,conf=0.35,device="cpu",verbose=False)[0]) for f in files]
for i in range(len(files)):
    for t_ in Tb[i]:
        w=t_[2]-t_[0]
        b=bk(w); buckets[b][1]+=1
        if any(iou(t_,s_)>0.5 for s_ in Sb[i]): buckets[b][0]+=1
for k,(h,t) in buckets.items():
    print(f"{k}: 召回 {100*h/max(1,t):.0f}%  ({h}/{t})")
