"""Executable entry point for PyInstaller.

Launches the Tkinter control panel by default. Pass ``--cli`` to use the old
command-line interface instead.
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
    argv = sys.argv[1:]

    if "--cli" in argv:
        argv = [a for a in argv if a != "--cli"]
        if not any(a == "--input-backend" or a.startswith("--input-backend=") for a in argv):
            import os
            if os.environ.get("BT_BRIDGE_HOST"):
                argv += ["--input-backend", "bt"]
        sys.argv = [sys.argv[0]] + argv
        from valaim.main import main as cli_main
        try:
            cli_main()
        finally:
            from valaim.input_ctrl import close_backend
            close_backend()
        return

    from valaim.webgui import main as gui_main
    gui_main()


if __name__ == "__main__":
    run()
