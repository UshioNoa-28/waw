"""Executable entry point for PyInstaller.

Importing ``valaim.main`` (rather than running ``main.py`` directly) keeps the
package's relative imports intact when the code is bundled into a single exe.
"""
import io
import sys


def _ensure_streams() -> None:
    """Provide no-op streams when frozen as a windowed app (sys.stdout is None)."""
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def run() -> None:
    _ensure_streams()
    argv = list(sys.argv)
    if not any(a == "--input-backend" or a.startswith("--input-backend=") for a in argv):
        # Prefer the Bluetooth bridge when a phone host is configured.
        import os
        if os.environ.get("BT_BRIDGE_HOST"):
            argv += ["--input-backend", "bt"]
        else:
            argv += ["--input-backend", "auto"]
    sys.argv = argv
    from valaim.main import main

    main()


if __name__ == "__main__":
    run()
