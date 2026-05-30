"""Standalone entry point for the comfy-bridge server.

Used by the packaged Windows .exe (PyInstaller, see comfy-bridge.spec) and runnable
directly with `python serve.py`. Reads host/port from .env via app.config and serves
app.main:app with uvicorn. The app object is passed to uvicorn.run directly (not an
import string) so it keeps working inside a frozen/one-file build.
"""
import os
import sys

# When frozen into a one-file exe, anchor the working directory to the exe's folder so
# load_dotenv() finds the .env shipped beside it and the asset-cache lands there too
# (Explorer double-click already sets this, but Task Scheduler / other launchers may not).
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

import uvicorn  # noqa: E402

from app.config import load_config  # noqa: E402
from app.main import app  # noqa: E402


def main() -> None:
    cfg = load_config()
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
