# VALORANT 蒸馏 round3 - 头特化 + 训练域对齐部署
# ============================================================
# 前置: Kaggle 右侧 Accelerator = **GPU T4 x2**, Settings 里 **Internet 打开**。
# 数据: 上传数据集(名字含 `round3` 或 `frames`),里面是纯图。标签由教师 yolo11m 现打。
# 相比 round2 的改动:
#   1) 训练域 = 部署域: 除全图外,额外生成"以头为中心、模拟准星窗口的裁剪"喂训练,专治小头;
#   2) imgsz 640(round2 是 512),双 T4 batch 翻倍;
#   3) 80 epoch + 自动按尺寸分桶的召回自评。

import subprocess, sys, os, glob, random, shutil
from pathlib import Path

def pip(*pkgs):
    try:
        __import__(pkgs[0].split("==")[0].replace("-","_"))
    except ImportError:
        subprocess.run([sys.executable,"-m","pip","install","-q",*pkgs],check=False)
pip("ultralytics"); pip("huggingface_hub"); import cv2, numpy as np

# ============================================================
# ## 1. 收图(你上传的 round3/frames 数据集优先,公开 haqi001 可选补足)

WORK = Path('/kaggle/working/ds'); WORK.mkdir(parents=True, exist_ok=True)
IMGS = WORK/'images'; IMGS.mkdir(exist_ok=True)
seen=set(); n=0
for root, dirs, files in os.walk('/kaggle/input'):
    if not any(k in root.lower() for k in ("round3","frame","replay","shot")):
        continue
    for fn in files:
        if not fn.lower().endswith((".jpg",".jpeg",".png")): continue
        p=os.path.join(root,fn); rp=os.path.realpath(p)
        if rp in seen: continue
        seen.add(rp)
        dst=IMGS/f'X{n:06d}.jpg'
        try: os.link(p,dst)
        except OSError: shutil.copy(p,dst)
        n+=1
print("uploaded images:", n)
assert n>=300, "没找到上传的 round3 数据集(应含纯图),先挂载再跑"

try:
    from huggingface_hub import hf_hub_download
    tmp='/kaggle/working/pub'; os.makedirs(tmp, exist_ok=True)
    for name in ("score.zip","source data.zip"):
        zp=hf_hub_download(repo_id='haqi001/VALORANT_destection_head_body_yolo', filename=name, repo_type='dataset', local_dir=tmp)
        with zipfile.ZipFile(zp) as z: pass
    import zipfile
    for name in ("score.zip","source data.zip"):
        with zipfile.ZipFile(tmp+"/"+name) as z: z.extractall(tmp+'/x')
    npub=0
    for f in glob.glob(tmp+'/x/**/*.jpg', recursive=True)+glob.glob(tmp+'/x/**/*.png', recursive=True):
        if npub<2000: shutil.copy(f, IMGS/f'P{npub:06d}.jpg'); npub+=1
    print("public extra:", npub)
except Exception as e:
    print("public skipped:", repr(e))

# 去重(内容哈希)
import hashlib
seen_h=set(); rem=0
for p in sorted(IMGS.glob('*')):
    h=hashlib.md5(p.read_bytes()).hexdigest()
    if h in seen_h: p.unlink(); rem+=1
    else: seen_h.add(h)
print("total after dedup:", len(list(IMGS.glob('*'))), "removed", rem)

# ============================================================
# ## 2. 教师标注(yolo11m@640, conf>=0.35) - 统一标注风格

import urllib.request
TEACHER='/kaggle/working/teacher.pt'
if not os.path.exists(TEACHER):
    urllib.request.urlretrieve('https://raw.githubusercontent.com/Rana-matcha/YOLO-valorant/main/best.pt', TEACHER)
from ultralytics import YOLO
t=YOLO(TEACHER); print("teacher classes:", t.names)

LAB=WORK/'labels'; LAB.mkdir(exist_ok=True)
files=sorted(IMGS.glob('*')); BATCH=8
for i in range(0,len(files),BATCH):
    b=files[i:i+BATCH]
    res=t.predict([str(p) for p in b], imgsz=640, conf=0.35, iou=0.5, device=0, verbose=False)
    if i%400==0: print(f"label {i}/{len(files)}", flush=True)
    for p,r in zip(b,res):
        lines=[]
        for bx in r.boxes:
            cls=int(bx.cls); x1,y1,x2,y2=[float(v) for v in bx.xyxy[0]]
            W,H=r.orig_shape[1], r.orig_shape[0]
            cx,cy,w,h=((x1+x2)/2/W),((y1+y2)/2/H),((x2-x1)/W),((y2-y1)/H)
            if 0<cx<1 and 0<cy<1 and 0.002<w<1 and 0.002<h<1:
                lines.append(f'{cls} {cx:.5f} {cy:.5f} {w:.5f} {h:.5f}')
        (LAB/(p.stem+'.txt')).write_text('\n'.join(lines))
keep=[p for p in files if (LAB/(p.stem+'.txt')).exists() and (LAB/(p.stem+'.txt')).stat().st_size>0]
print("labeled:", len(keep))

# ============================================================
# ## 3. 裁剪增广: 模拟部署的准星窗口,把头放大

# 全帧标签 -> 读回像素框做裁剪重投影
CROP=WORK/'crops'; CROP.mkdir(exist_ok=True)
CLAB=WORK/'croplabels'; CLAB.mkdir(exist_ok=True)
n_crop=0
def load_px(p): return cv2.imread(str(p))
random.seed(3)
for p in keep:
    txt=(LAB/(p.stem+'.txt')).read_text().splitlines()
    boxes=[]
    for L in txt:
        _,cx,cy,w,h=[float(v) for v in L.split()]
        boxes.append((int(L.split()[0]),cx,cy,w,h))
    img=load_px(p)
    if img is None: continue
    H,W=img.shape[:2]
    heads=[b for b in boxes if b[0]==1]
    if not heads: continue
    for _ in range(2):                      # 每张最多 2 个窗
        hx,hy = random.choice(heads)[1], random.choice(heads)[2]
        px,py = hx*W, hy*H
        win = random.randint(560, 1000)     # 模拟 840px 级别准星窗
        x0=max(0,min(W-win, int(px-win//2))); y0=max(0,min(H-win, int(py-win//2)))
        sub=img[y0:y0+win if y0+win<=H else H, x0:x0+win if x0+win<=W else W]
        sw,sh=sub.shape[1],sub.shape[0]
        if sw<256 or sh<256: continue
        lines=[]
        for cls,cx,cy,w,h in boxes:
            bx1=(cx-w/2)*W-x0; by1=(cy-h/2)*H-y0; bw=w*W; bh=h*H
            ncx=(bx1+bw/2)/sw; ncy=(by1+bh/2)/sh; nw=bw/sw; nh=bh/sh
            if 0<ncx<1 and 0<ncy<1 and 0.004<nw<1 and 0.004<nh<1:
                lines.append(f'{cls} {ncx:.5f} {ncy:.5f} {nw:.5f} {nh:.5f}')
        if not lines: continue
        name=f'C{n_crop:06d}.jpg'; cv2.imwrite(str(CROP/name), sub, [cv2.IMWRITE_JPEG_QUALITY,90])
        (CLAB/name.replace('.jpg','.txt')).write_text('\n'.join(lines)); n_crop+=1
print("crop samples:", n_crop)

# ============================================================
# ## 4. 汇总 train/val + yaml (全帧 + 裁剪一起; 按 8:1 分)

POOL=WORK/'all_img'; POOL_L=WORK/'all_lab'; POOL.mkdir(exist_ok=True); POOL_L.mkdir(exist_ok=True)
for p in keep:
    shutil.copy(p, POOL/p.name) if not (POOL/p.name).exists() else None
    shutil.copy(LAB/(p.stem+'.txt'), POOL_L/(p.stem+'.txt'))
for cp in CROP.glob('*.jpg'):
    shutil.copy(cp, POOL/cp.name); shutil.copy(CLAB/(cp.stem+'.txt'), POOL_L/(cp.stem+'.txt'))
allf=sorted(POOL.glob('*.jpg'))
random.shuffle(allf); nv=max(50,len(allf)//9)
for sp,lst in {'train':allf[nv:],'val':allf[:nv]}.items():
    d=WORK/sp; (d/'images').mkdir(parents=True,exist_ok=True); (d/'labels').mkdir(parents=True,exist_ok=True)
    for p in lst:
        shutil.copy(p, d/'images'/p.name); shutil.copy(POOL_L/(p.stem+'.txt'), d/'labels'/(p.stem+'.txt'))
YAML='/kaggle/working/valorant3.yaml'
open(YAML,'w').write(f"path: {WORK}\ntrain: train/images\nval: val/images\nnc: 2\nnames: [Body, Head]\n")
print("train", len(allf)-nv, "val", nv)

# ============================================================
# ## 5. 训练 yolo26n @640 (双 T4;batch 64,80 epoch)

ARCH='yolo26n.pt'
s=YOLO(ARCH)
s.train(data=YAML, epochs=80, imgsz=640, batch=64, device=[0,1], workers=8,
        patience=25, cos_lr=True, close_mosaic=15, project='/kaggle/working/run',
        name='student3', exist_ok=True)

# ============================================================
# ## 6. 导出 ONNX(640 动态)+ best.pt + 分桶召回自评 -> zip

best='/kaggle/working/run/student3/weights/best.pt'
m=YOLO(best)
onnx_p=m.export(format='onnx', imgsz=640, opset=12, dynamic=True, simplify=True)
out=Path('/kaggle/working/val_student3'); (out/'models/student3').mkdir(parents=True, exist_ok=True)
shutil.copy(onnx_p, out/'models/student3/model.onnx')
(out/'models/student3/model.json').write_text('{"names": {"0": "Body", "1": "Head"}, "imgsz": 640, "dynamic": true}')
shutil.copy(best, out/'best.pt')

# 自评: val 上按头宽分桶召回(教师640当参考)
import math
vtiles=sorted((WORK/'val/images').glob('*'))[:120]
Tb=[t.predict(str(f),imgsz=1280,conf=0.28,device=0,verbose=False)[0] for f in vtiles]
def bxs(r): return [[float(v) for v in b.xyxy[0]] for b in r.boxes]
def iou(a,b):
    ix=min(a[2],b[2])-max(a[0],b[0]); iy=min(a[3],b[3])-max(a[1],b[1])
    if ix<=0 or iy<=0: return 0.0
    i=ix*iy; u=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-i
    return i/u if u>0 else 0.0
buck={"<20":[0,0],"20-40":[0,0],"40-80":[0,0],">80":[0,0]}
def bk(w): return "<20" if w<20 else "20-40" if w<40 else "40-80" if w<80 else ">80"
for i,f in enumerate(vtiles):
    sb=bxs(m.predict(str(f),imgsz=640,conf=0.35,device=0,verbose=False)[0])
    for tb_ in bxs(Tb[i]):
        w=tb_[2]-tb_[0]; k=bk(w); buck[k][1]+=1
        if any(iou(tb_,s_)>0.5 for s_ in sb): buck[k][0]+=1
print("== round3 按尺寸召回 ==")
for k,(h,tt) in buck.items(): print(f"  {k}px: {100*h/max(1,tt):.0f}% ({h}/{tt})")
subprocess.run(['zip','-rq','val_student3.zip','val_student3'], cwd='/kaggle/working')
print("DONE -> 下载 /kaggle/working/val_student3.zip; 把 model.onnx 给我, 我放进 models/ 并改默认+重编 exe")
