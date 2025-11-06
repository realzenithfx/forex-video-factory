# make_videos.py — NEXT-10 scheduler, hardened logging, no-crash
import os, csv, json, random, math, time, subprocess, traceback
from pathlib import Path
from datetime import datetime, timedelta
import pytz, requests

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (VideoFileClip, ImageClip, AudioFileClip,
                            CompositeVideoClip, concatenate_videoclips, ColorClip)
from moviepy.video.fx.all import crop
from moviepy.audio.fx.all import volumex

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

CSV_PATH   = Path("prompts.csv")
MUSIC_DIR  = Path("music")
OUT_DIR    = Path("renders")
TMP_DIR    = Path("tmp")
STATE_FILE = Path("posted_state.json")

W, H = 1080, 1920
TARGET_DURATION = 32  # seconds

PEXELS_API_KEY   = os.getenv("PEXELS_API_KEY", "")
YT_CLIENT_ID     = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True); TMP_DIR.mkdir(exist_ok=True)

def tz_pt(): return pytz.timezone("America/Los_Angeles")
def now_pt(): return datetime.now(tz_pt())

def parse_pt(s):
    return tz_pt().localize(datetime.strptime(s, "%Y-%m-%d %H:%M"))

def to_utc_rfc3339_from_pt(s):
    return parse_pt(s).astimezone(pytz.utc).isoformat()

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"posted": []}

def save_and_commit_state(state, msg):
    STATE_FILE.write_text(json.dumps(state, indent=2))
    try:
        subprocess.run(["git","config","user.email","bot@github.actions"],check=True)
        subprocess.run(["git","config","user.name","gh-actions-bot"],check=True)
        subprocess.run(["git","add",str(STATE_FILE)],check=True)
        subprocess.run(["git","commit","-m",msg],check=True)
        subprocess.run(["git","push"],check=True)
    except Exception as e:
        print("State commit skipped:", e)

def pick_music():
    if not MUSIC_DIR.exists(): return None
    tr = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3",".wav",".m4a"}]
    return random.choice(tr) if tr else None

# ---- Pexels (verified API) ----
# Docs: https://www.pexels.com/api/documentation/  License: free to use, attribution not required.   
- **Uploads:** We use `videos.insert` — the official upload endpoint. :contentReference[oaicite:3]{index=3}  
- **Pexels:** API + license allow free commercial use (no attribution required). :contentReference[oaicite:4]{index=4}  
- **Cron (hands-off):** Scheduled workflows use POSIX cron in **UTC**; hourly is fine and reliable. :contentReference[oaicite:5]{index=5}

---

## Two quick checks now

1) **Replace** the file in your repo with the script above → Commit.  
2) Open the **latest failed run** → click **build → Run generator**. With this script you’ll see:
   - `Secrets present -> ... True/False`  
   - `Scheduling this run: X video(s).`  
   - `Rendered: renders/...mp4`  
   - If upload fails: `YouTube upload error (skipped): ...` (but the run **stays green** and the MP4 is attached as an artifact if you left that step in the workflow).

> If you see an error like `invalid_grant` or “refresh token expired,” your Google Cloud **OAuth consent screen is in “Testing”**, which makes refresh tokens expire **after 7 days**. Switch to **Production** to stop the 7-day expiry, then mint a new refresh token. (Google docs & threads.) :contentReference[oaicite:6]{index=6}

---

If a run still fails, copy just the **first red error line** from **Run generator** and I’ll map it exactly (auth, quota, timing, or network) and patch it.
::contentReference[oaicite:7]{index=7}
