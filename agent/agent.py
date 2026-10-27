import logging
import os
import platform
import subprocess
import sys
import time
 
import requests
from dotenv import load_dotenv


load_dotenv()
 
PANEL_URL = os.environ.get("PANEL_URL", "").rstrip("/")
PANEL_TOKEN = os.environ.get("PANEL_TOKEN", "")
INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "15"))  # seconds
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
MAX_BACKOFF = 60
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("agent")


def build_shutdown_cmd() -> list[str]:
    if platform.system() == "Windows":
        return ["shutdown", "/s", "/t", "5"]
    cmd = ["shutdown", "-h", "now"]
    
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd
    return cmd


def do_shutdown():
    cmd = build_shutdown_cmd()
    if DRY_RUN:
        log.warning("DRY RUN: would run %s", cmd)
        return
    log.warning("Running: %s", cmd)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Shutdown failed (%s): %s", result.returncode, result.stderr.strip())



COMMANDS = {
    "shutdown": do_shutdown,
}


def handle_command(name: str):
    action = COMMANDS.get(name)
    if action is None:
        log.error("Ignoring unknown command from server: %r", name)
        return
    action()



def main():

    if not PANEL_URL or not PANEL_TOKEN:
        sys.exit("Set PANEL_URL and PANEL_TOKEN environment variables.")


    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {PANEL_TOKEN}"


    delay = INTERVAL
    while True:
        try:
            r = session.post(f"{PANEL_URL}/api/heartbeat", timeout=10)
            if r.status_code == 401:
                log.error("Server rejected our token (401). Check PANEL_TOKEN.")

            else:
                r.raise_for_status()
                command = r.json().get("command")
                if command:
                    log.info("Recieved command: %s", command)
                    handle_command(command)

            delay = INTERVAL

        except requests.RequestException as e:

            delay = min(delay * 2, MAX_BACKOFF)
            log.warning("Heartbeat failed (%s). Retrying in %ss", e.__class__.__name__, delay)

        time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped.")