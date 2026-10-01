import glob, random, math
from ultralytics import YOLO
ROOT="/home/anna/code/projects/val"
files=sorted(glob.glob(f"{ROOT}/distill_data/test_frames/*.jpg")+glob.glob(f"{ROOT}/distill_data/test_frames/*.png"))
random.Random(11).shuffle(files); files=files[:150]
T=YOLO(f"{ROOT}/models/valorant_yolo11m/model.onnx")
def boxes(r): return [[float(v) for v in b.xyxy[0]] for b in r.boxes]
def iou(a,b):
    ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
    if ix<=0 or iy<=0: return 0.0
    i=ix*iy; u=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i
    return i/u if u>0 else 0.0
print("teacher 640 ...", flush=True)
rT=[T.predict(f, imgsz=640, conf=0.35, device="cpu", verbose=False)[0] for f in files]
Tb=[boxes(r) for r in rT]
for nm,path in [("R1","models/valorant_v26s/model.onnx"),("R2","models/valorant_v26s_r2/model.onnx")]:
    m=YOLO(f"{ROOT}/{path}")
    print(nm, "512 ...", flush=True)
    hits=tot=fp=thf=0
    for i,f in enumerate(files):
        mb=boxes(m.predict(f, imgsz=512, conf=0.35, device="cpu", verbose=False)[0])
        tb=Tb[i]; tot+=len(mb); thf+=len(tb)
        for b_ in mb:
            if any(iou(b_,t_)>0.5 for t_ in tb): hits+=1
            else: fp+=1
        rec=sum(1 for t_ in tb if any(iou(b_,t_)>0.5 for b_ in mb))
        globals().setdefault('recs',[]).append((rec,len(tb)))
    rr=sum(x for x,_ in globals()['recs']); rc=sum(y for _,y in globals()['recs'])
    print(f"{nm}: 对教师召回 {100*rr/max(1,rc):.0f}%  误检率 {100*fp/max(1,tot):.0f}%  (教师框{rc} 学生框{tot})", flush=True)
    globals()['recs']=[]
print("EVAL2 DONE", flush=True)
