import os
from pathlib import Path

# Load from .env if it exists
env_path = Path(".env")
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
FB_PAGE_ID      = os.getenv("FB_PAGE_ID")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
DASHBOARD_PORT  = int(os.getenv("PORT", "8080"))

