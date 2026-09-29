"""Resolve bundled resources (models, assets) whether running from source or a PyInstaller exe."""
import sys
from pathlib import Path


def resource_root() -> Path:
    """Base directory for bundled resources.

    - PyInstaller (onefile/onedir): extracted files live under ``sys._MEIPASS``.
    - Frozen but no _MEIPASS (edge cases): fall back to the exe's directory.
    - Source mode: the project root (parent of the ``valaim`` package).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_model_path(path: str) -> str:
    """Return a model path that exists, checking CWD then the resource root.

    Lets the default ``models/...`` path work from the project root when running
    from source and from the bundled copy when frozen.
    """
    p = Path(path)
    if p.is_absolute():
        return str(p) if p.exists() else str(p)
    if p.exists():
        return str(p.resolve())
    bundled = resource_root() / p
    if bundled.exists():
        return str(bundled)
    return str(p)
