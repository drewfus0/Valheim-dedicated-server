#!/usr/bin/env python3
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.resolve()
WEB_DIR = ROOT_DIR / "webmanager"

# Look for venv in webmanager/.venv or .venv
VENV_PYTHON = WEB_DIR / ".venv" / "bin" / "python3"
if not VENV_PYTHON.exists():
    VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python3"

# Re-exec inside the virtual environment if not already running in it
if VENV_PYTHON.exists() and sys.executable != str(VENV_PYTHON):
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:])

# Add webmanager to sys.path so app and core modules resolve cleanly
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import uvicorn

if __name__ == "__main__":
    from core.system import configure_system_power_and_performance

    configure_system_power_and_performance()
    print("[Launcher] Starting Valheim Web Manager from webmanager/ (Port 8080)...")
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
        app_dir=str(WEB_DIR),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
