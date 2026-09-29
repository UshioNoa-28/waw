# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ValAim standalone exe.

Build (on Windows):  pyinstaller valaim.spec
Produces:            dist/ValAim.exe  (self-contained; the default model is bundled)
"""
import os

from PyInstaller.utils.hooks import collect_all

BASE = os.path.dirname(os.path.abspath(SPEC))
MODEL_DIR = os.path.join(BASE, "models", "valorant_head_body")
MODEL_REL = os.path.join("models", "valorant_head_body")

datas = []
binaries = []
hiddenimports = []

for pkg in ("onnxruntime", "cv2", "mss", "numpy"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Bundle the default model so the exe runs standalone.
for fname in ("model.onnx", "model.json"):
    src = os.path.join(MODEL_DIR, fname)
    if os.path.exists(src):
        datas.append((src, MODEL_REL))

a = Analysis(
    [os.path.join(BASE, "aimbot.py")],
    pathex=[BASE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ValAim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # set to False for a silent (no console window) build
)
