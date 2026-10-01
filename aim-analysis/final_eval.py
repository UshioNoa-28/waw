import glob, math, os, random, sys
import cv2, numpy as np, onnxruntime as ort

ROOT="/home/anna/code/projects/val"
def sess(p):
    return ort.InferenceSession(p, providers=["CPUExecutionProvider"])
def letterbox(img, sz):
    h,w=img.shape[:2]; r=min(sz/h, sz/w)
    nh,nw=int(round(w*r)),int(round(h*r))
    canvas=np.full((sz,sz,3),114,np.uint8)
    canvas[:nh,:nw]=cv2.resize(img,(nw,nh))
    return canvas, r, 0, 0
def decode(out, r, conf):
    o=out[0].T
    cands=[]
    for row in o:
        cls=int(np.argmax(row[4:6])); c=row[4+cls]
        if c<conf: continue
        cx,cy,bw,bh=row[:4]
        cands.append((cx-bw/2,cy-bh/2,cx+bw/2,cy+bh/2,c,cls))
    cands.sort(key=lambda x:-x[4])
    fin=[]
    for b in cands:
        if all(iou(b,f)==0.0 or f[5]!=b[5] for f in fin):
            fin.append((b[0]/r,b[1]/r,b[2]/r,b[3]/r,b[4],b[5]))
    return fin

def iou(a,b):
    ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
    if ix<=0 or iy<=0: return 0.0
    inter=ix*iy; u=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/u if u>0 else 0.0

T=sess(f"{ROOT}/models/valorant_yolo11m/model.onnx")
models={n:sess(p) for n,p in [("R1",f"{ROOT}/models/valorant_v26s/model.onnx"),
                              ("R2",f"{ROOT}/models/valorant_v26s_r2/model.onnx")]}
files=sorted(glob.glob(f"{ROOT}/distill_data/test_frames/*.jpg")+glob.glob(f"{ROOT}/distill_data/test_frames/*.png"))
random.Random(11).shuffle(files); files=files[:120]
def run(m,sz):
    res=[]
    for f in files:
        img=cv2.imread(f); lb,r,_,_=letterbox(img,sz)
        blob=lb[:,:,::-1].transpose(2,0,1)[None].astype(np.float32)/255
        o=m.run(None,{m.get_inputs()[0].name:blob})[0]
        res.append(decode(o,r,0.35))
    return res
Tb=run(T,640)
for nm,m in models.items():
    sb=run(m,512)
    tr=tot=fp=thf=0
    for ta,sa in zip(Tb,sb):
        tot+=len(sa); thf+=len(ta)
        for s_ in sa:
            if any(iou(s_,t_)>0.5 for t_ in ta): tr+=1
            else: fp+=1
    hit=0
    for ta,sa in zip(Tb,sb):
        for t_ in ta:
            if any(iou(s_,t_)>0.5 for s_ in sa): hit+=1
    print(f"{nm}: 对教师召回 {100*hit/max(1,thf):.0f}%  误检率 {100*fp/max(1,tot):.0f}%  (教师框{thf} 学生框{tot})", flush=True)
print("EVAL DONE", flush=True)
