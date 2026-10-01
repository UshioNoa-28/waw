# VALORANT 蒸馏 round2.3
# ============================================================
# ## VALORANT 蒸馏: yolo11m 教师 → yolo11n 学生
# 前置: Kaggle 右侧 Accelerator 选 **GPU T4 x2**, Settings 里 **Internet 打开**。
# 数据来源(都可选,缺哪个自动跳): ①你上传的 frames 数据集(名字含 `frames`) ②HF 公开 haqi001 head/body 3.2k 张

import subprocess, sys, os, glob, random, shutil
from pathlib import Path
try:
    import ultralytics
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","-q","ultralytics"],check=False)
try:
    import huggingface_hub
except ImportError:
    subprocess.run([sys.executable,"-m","pip","install","-q","huggingface_hub"],check=False)

WORK = Path('/kaggle/working/ds'); WORK.mkdir(parents=True, exist_ok=True)
IMGS = WORK/'images'; IMGS.mkdir(exist_ok=True)
seen_real=set(); n_img=0
for root, dirs, files in os.walk('/kaggle/input'):
    if not any(k in root.lower() for k in ("frame","replay","shot")):
        continue
    for fn in files:
        if not fn.lower().endswith((".jpg",".jpeg",".png")): continue
        p=os.path.join(root,fn)
        rp=os.path.realpath(p)
        if rp in seen_real: continue
        seen_real.add(rp)
        dst=IMGS/f'X{n_img:06d}.jpg'
        try: os.link(p,dst)
        except OSError: shutil.copy(p,dst)
        n_img+=1
print('collected images:', n_img)

try:
    from huggingface_hub import hf_hub_download
    import zipfile
    tmp='/kaggle/working/pub'
    os.makedirs(tmp, exist_ok=True)
    for name in ("score.zip","source data.zip"):
        zp=hf_hub_download(repo_id='haqi001/VALORANT_destection_head_body_yolo', filename=name, repo_type='dataset', local_dir=tmp)
        with zipfile.ZipFile(zp) as z: z.extractall(tmp+'/x')
    n_pub=0
    for f in glob.glob(tmp+'/x/**/*.jpg', recursive=True)+glob.glob(tmp+'/x/**/*.png', recursive=True)+glob.glob(tmp+'/x/**/*.jpeg', recursive=True):
        if n_pub<3200:
            shutil.copy(f, IMGS/f'P{n_pub:06d}.jpg'); n_pub+=1
    print('public imgs:', n_pub)
except Exception as e:
    print('public dataset skipped:', repr(e))
print('total images:', len(list(IMGS.iterdir())))
assert len(list(IMGS.iterdir()))>=300, '图片太少(检查两个数据集是否都挂载)'


# ============================================================
# ## 教师自动标注(yolo11m@640, conf≥0.45;公开集自带标签的图也统一用教师重标,保证标注风格一致)

# 近重复防护: 完全相同哈希的帧去重(随机间隔已降低相关性)
import hashlib
seen = set(); removed = 0
for p in sorted(IMGS.glob('*')):
    h = hashlib.md5(p.read_bytes()).hexdigest()
    if h in seen:
        p.unlink(); removed += 1
    else:
        seen.add(h)
print('exact-dup removed:', removed)

import urllib.request
TEACHER = '/kaggle/working/teacher.pt'
if not os.path.exists(TEACHER):
    urllib.request.urlretrieve('https://raw.githubusercontent.com/Rana-matcha/YOLO-valorant/main/best.pt', TEACHER)
from ultralytics import YOLO
t = YOLO(TEACHER)
print('teacher classes:', t.names)

LAB = WORK/'labels'; LAB.mkdir(exist_ok=True)
files = sorted(IMGS.glob('*'))
BATCH = 8
for i in range(0, len(files), BATCH):
    b = files[i:i+BATCH]
    res = t.predict([str(p) for p in b], imgsz=640, conf=0.35, iou=0.5, device=0, verbose=False)
    if i % 200 == 0: print(f"label {i}/{len(files)}", flush=True)
    for p, r in zip(b, res):
        lines = []
        for bx in r.boxes:
            cls = int(bx.cls); x1,y1,x2,y2 = [float(v) for v in bx.xyxy[0]]
            W,H = r.orig_shape[1], r.orig_shape[0]
            cx,cy,w,h = ((x1+x2)/2/W), ((y1+y2)/2/H), ((x2-x1)/W), ((y2-y1)/H)
            if 0 < cx < 1 and 0 < cy < 1 and 0.002 < w < 1 and 0.002 < h < 1:
                lines.append(f'{cls} {cx:.5f} {cy:.5f} {w:.5f} {h:.5f}')
        (LAB / (p.stem + '.txt')).write_text('\n'.join(lines))
keep = [p for p in files if (LAB/(p.stem+'.txt')).exists() and (LAB/(p.stem+'.txt')).stat().st_size>0]
print('labeled with objects:', len(keep))

# ============================================================
# ## 划 train/val + 写 data.yaml

random.seed(7); random.shuffle(keep)
n_val = max(30, len(keep)//8)
split = {'train': keep[n_val:], 'val': keep[:n_val]}
for sp, lst in split.items():
    d = WORK/sp
    (d/'images').mkdir(parents=True, exist_ok=True)
    (d/'labels').mkdir(parents=True, exist_ok=True)
    for p in lst:
        if not (d/'images'/p.name).exists(): shutil.copy(p, d/'images'/p.name)
        shutil.copy(LAB/(p.stem+'.txt'), (d/'labels')/(p.stem+'.txt'))
YAML = '/kaggle/working/valorant.yaml'
open(YAML,'w').write(f"""path: {WORK}
train: train/images
val: val/images
nc: 2
names: [Body, Head]
""")
print('train', len(split['train']), 'val', len(split['val']))


# ============================================================
# ## 训练 yolo11n(60 epoch, imgsz 512;双 T4 约 1~2h;显存不够就 batch=16)

ARCH = 'yolo26n.pt'   # 主选: NMS-free, 更小更快; 若导出后 DML 跑不动, 改回 'yolo11n.pt' 再跑一次
s = YOLO(ARCH)
s.train(data=YAML, epochs=60, imgsz=512, batch=32, device=[0], workers=8,
        patience=20, cos_lr=True, close_mosaic=12, project='/kaggle/working/run', name='student', exist_ok=True)

# ============================================================
# ## 导出动态 ONNX(opset12)→ 输出 zip 下载

best = '/kaggle/working/run/student/weights/best.pt'
m = YOLO(best)
onnx_p = m.export(format='onnx', imgsz=512, opset=12, dynamic=True, simplify=True)
import shutil, subprocess
out = Path('/kaggle/working/val_student'); (out/'models/student').mkdir(parents=True, exist_ok=True)
shutil.copy(onnx_p, out/'models/valorant_v11n/model.onnx')
(out/'models/valorant_v11n/model.json').write_text('{"names": {"0": "Body", "1": "Head"}, "imgsz": 512, "dynamic": true}')
shutil.copy(best, out/'teacher_free_best.pt')
subprocess.run(['zip','-rq','val_student.zip','val_student'], cwd='/kaggle/working')
print('DONE -> 下载 /kaggle/working/val_student.zip, 把 model.onnx 丢给我或放 WSL 仓库 models/ 下')
