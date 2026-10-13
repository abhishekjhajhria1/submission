"""Start everything: the sandbox farm app (internal :8001) and the FarmHand dashboard.

Locally: python run.py  -> http://127.0.0.1:8000
Hosted:  the platform sets PORT; the dashboard binds 0.0.0.0:$PORT and the farm app stays internal.
"""
import os
import subprocess
import sys
import time
import webbrowser

import uvicorn

if __name__ == "__main__":
    hosted = bool(os.getenv("PORT"))
    port = int(os.getenv("PORT", "8000"))
    farm = subprocess.Popen([sys.executable, "-m", "uvicorn", "farm_app.app:app", "--host", "127.0.0.1",
                             "--port", "8001", "--log-level", "warning"])
    try:
        time.sleep(1.5)
        print(f"FarmHand dashboard: http://127.0.0.1:{port}   (farm office view: /farm)")
        if not hosted and "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{port}")
        uvicorn.run("dashboard.server:app", host="0.0.0.0" if hosted else "127.0.0.1", port=port, log_level="warning")
    finally:
        farm.terminate()
