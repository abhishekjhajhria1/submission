"""Start everything: the sandbox farm app (:8001) and the FarmHand dashboard (:8000)."""
import subprocess
import sys
import time
import webbrowser

import uvicorn

if __name__ == "__main__":
    farm = subprocess.Popen([sys.executable, "-m", "uvicorn", "farm_app.app:app", "--port", "8001",
                             "--log-level", "warning"])
    try:
        time.sleep(1.5)
        print("Farm office:  http://127.0.0.1:8001\nDashboard:    http://127.0.0.1:8000")
        if "--no-browser" not in sys.argv:
            webbrowser.open("http://127.0.0.1:8000")
        uvicorn.run("dashboard.server:app", port=8000, log_level="warning")
    finally:
        farm.terminate()
