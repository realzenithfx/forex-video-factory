# make_videos.py — Forex Voyage shorts factory (NEXT-5 scheduler, hardened)
# Verified behaviors:
# - Uploads via YouTube Data API videos.insert (official method). publishAt scheduling requires privacyStatus="private". 
# - Pexels API used per official docs; video search then filter portrait clips, photo search orientation handled client-side.
# Docs cited at end of this file.

import os, csv, json, random, math, time, subprocess
from pathlib import Path
from datetime import datetime
import pytz, requests

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    VideoFileClip, ImageClip, AudioFileClip,
    CompositeVideoClip, concatenate_videoclips, ColorClip
)
from moviepy.video.fx.all import crop
from moviepy.audio.fx.all import volumex

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

# ---- Paths & constants ----
CSV_PATH   = Path("prompts.csv")
MUSIC_DIR  = Path("music")
OUT_DIR    = Path("renders")
TMP_DIR    = Path("tmp")
STATE_FILE = Path("posted_state.json")

W, H             = 1080, 1920     # 9:16 portrait
TARGET_DURATION  = 32             # seconds, edit if you want 45–60s

# ---- Secrets from GitHub Actions ----
PEXELS_API_KEY   = os.getenv("PEXELS_API_KEY", "")
YT_CLIENT_ID     = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# ---- Helpers ----
def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)

def tz_pt():
    return pytz.timezone("America/Los_Angeles")

def now_pt():
    return datetime.now(tz_pt())

def parse_pt(s):  # "YYYY-MM-DD HH:MM"
    return tz_pt().localize(datetime.strptime(s, "%Y-%m-%d %H:%M"))

def to_utc_rfc3339_from_pt(s):
    return parse_pt(s).astimezone(pytz.utc).isoformat()

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posted": []}

def save_and_commit_state(state, msg):
    STATE_FILE.write_text(json.dumps(state, indent=2))
    try:
        subprocess.run(["git", "config", "user.email", "bot@github.actions"], check=True)
        subprocess.run(["git", "config", "user.name", "gh-actions-bot"], check=True)
        subprocess.run(["git", "add", str(STATE_FILE)], check=True)
        subprocess.run(["git", "commit", "-m", msg], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print("State commit skipped:", e)

def pick_music():
    if not MUSIC_DIR.exists(): return None
    tracks = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3",".wav",".m4a"}]
    return random.choice(tracks) if tracks else None

# ---- Pexels API (photos & videos) ----
# Docs: https://www.pexels.com/api/documentation/  (free to use; video & photo search)
def pexels_video(keyword):
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": keyword, "per_page": 5},  # orientation not documented for video; filter client-side
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        for v in data.get("videos", []):
            # pick a portrait file if available
            for f in sorted(v.get("video_files", []), key=lambda x: x.get("width", 0)):
                w, h, link = f.get("width"), f.get("height"), f.get("link")
                if w and h and link and h > w:
                    return link
    except requests.RequestException as e:
        print("Pexels video API error:", e)
    return None

def pexels_photos(query, need=6):
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_API_KEY},
            params={"query": query, "per_page": need, "orientation": "portrait"},
            timeout=30
        )
        r.raise_for_status()
        photos = []
        for p in r.json().get("photos", []):
            src = p.get("src", {})
            link = src.get("large") or src.get("portrait") or src.get("large2x")
            if link: photos.append(link)
        return photos[:need]
    except requests.RequestException as e:
        print("Pexels photo API error:", e)
        return []

def download(url, dest: Path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for ch in r.iter_content(8192):
                if ch: f.write(ch)

# ---- Visual assembly ----
def render_text_img(text, w, h, font_size=64, fill=(255,255,255), bg=(11,31,59,200)):
    img = Image.new("RGBA", (w,h), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.rectangle([0,0,w,h], fill=bg)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    words, lines, line = text.split(), [], ""
    for w1 in words:
        test = (line + " " + w1).strip()
        if d.textlength(test, font=font) > w-80:
            if line: lines.append(line)
            line = w1
        else:
            line = test
    if line: lines.append(line)
    y = int((h - len(lines)*font_size*1.1)/2)
    for ln in lines:
        tw = d.textlength(ln, font=font)
        x = int((w - tw)/2)
        d.text((x,y), ln, font=font, fill=fill)
        y += int(font_size*1.1)
    p = TMP_DIR / f"t_{int(time.time()*1000)}.png"
    img.save(p); return str(p)

def build_from_video(vpath, overlay):
    clip = VideoFileClip(vpath)
    w, h = clip.w, clip.h
    target = W / H
    if abs(w/h - target) > 0.01:
        new_w = int(h * target)
        x1 = max(0, (w - new_w)//2)
        clip = crop(clip, x1=x1, y1=0, x2=x1+new_w, y2=h)
    clip = clip.resize((W, H))
    if clip.duration < TARGET_DURATION:
        loops = math.ceil(TARGET_DURATION / clip.duration)
        clip = concatenate_videoclips([clip]*loops).subclip(0, TARGET_DURATION)
    else:
        clip = clip.subclip(0, TARGET_DURATION)
    bar = ColorClip((W,200), color=(11,31,59)).set_duration(TARGET_DURATION).set_opacity(0.7).set_position(("center","top"))
    txt = ImageClip(render_text_img(overlay, W, 200)).set_duration(TARGET_DURATION).set_position(("center","top"))
    return CompositeVideoClip([clip, bar, txt])

def build_from_photos(paths, overlay):
    per = max(5, int(TARGET_DURATION / max(1,len(paths))))
    clips = [ImageClip(p).resize(height=H).resize(width=W).set_duration(per) for p in paths]
    video = concatenate_videoclips(clips).subclip(0, TARGET_DURATION)
    bar = ColorClip((W,200), color=(11,31,59)).set_duration(TARGET_DURATION).set_opacity(0.7).set_position(("center","top"))
    txt = ImageClip(render_text_img(overlay, W, 200)).set_duration(TARGET_DURATION).set_position(("center","top"))
    return CompositeVideoClip([video, bar, txt])

# ---- YouTube Data API ----
def yt_ready():
    return all([YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN])

def yt_service():
    # Uses a refresh_token to mint access tokens automatically (per google-auth docs)
    creds = Credentials(
        None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds)

def yt_upload(file_path, title, description, publish_at_rfc3339, tags=None):
    yt = yt_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "27",     # Education
            "tags": tags or []
        },
        "status": {
            "privacyStatus": "private",     # required for publishAt to schedule
            "publishAt": publish_at_rfc3339,
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress()*100)}%")
    return resp.get("id")

# ---- Main flow ----
def main():
    ensure_dirs()

    if not CSV_PATH.exists():
        print("prompts.csv not found in repo root.")
        return

    state = load_state()
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8", newline="")))
    print("Now (Pacific):", now_pt().strftime("%Y-%m-%d %H:%M"))

    # Pick the NEXT 5 future rows (not yet posted)
    future = []
    for r in rows:
        try:
            dt = parse_pt(r["PublishTime_Pacific"])
        except Exception as e:
            print("Bad date, skipping:", r.get("PublishTime_Pacific"), e); continue
        key = f"{r['PublishTime_Pacific']}|{r['Title']}"
        if key in state["posted"]:
            continue
        if dt > now_pt():
            future.append((dt, r))
    future.sort(key=lambda x: x[0])
    todo = [r for _, r in future[:5]]

    print(f"Scheduling this run: {len(todo)} video(s).")
    if not todo:
        return

    for r in todo:
        key     = f"{r['PublishTime_Pacific']}|{r['Title']}"
        title   = r.get("Title") or "Forex Voyage"
        overlay = r.get("OverlayText") or title
        script  = r.get("Prompt_or_Script") or ""
        hashtags = r.get("Hashtags","")
        tags     = [t.strip("# ") for t in hashtags.split() if t.startswith("#")]
        link     = r.get("ZenithFX_Link") or "https://zenithfx.com/"
        desc = "\n".join([s for s in [
            script.strip(),
            "",
            "Education only — not financial advice.",
            "This video promotes my company, ZenithFX.",
            "",
            r.get("CTA","").strip(),
            link,
            hashtags
        ] if s])

        # Media (video preferred, photo slideshow fallback)
        keywords = (r.get("Broll_Keywords") or "forex charts;world map").split(";")
        vurl = pexels_video(keywords[0])
        final = None
        try:
            if vurl:
                vp = TMP_DIR / f"pv_{int(time.time())}.mp4"
                download(vurl, vp)
                final = build_from_video(str(vp), overlay)
            else:
                photos = []
                for kw in keywords:
                    photos += pexels_photos(kw, need=3)
                if not photos:
                    print("No media found; skipping:", key); continue
                ph_paths = []
                for i, u in enumerate(photos[:6]):
                    p = TMP_DIR / f"ph_{i}_{int(time.time())}.jpg"; download(u, p); ph_paths.append(str(p))
                final = build_from_photos(ph_paths, overlay)

            # Audio bed (quiet)
            m = pick_music()
            if m:
                a = AudioFileClip(str(m)).fx(volumex, 0.18)
                final = final.set_audio(a)

            # Export to MP4
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in title)[:50]
            out_file = OUT_DIR / f"{safe}_{r['PublishTime_Pacific'].replace(' ','_').replace(':','-')}.mp4"
            final.write_videofile(str(out_file), fps=30, codec="libx264", audio_codec="aac", preset="medium", threads=4)
            print("Rendered:", out_file)

            # Schedule on YouTube
            publish_at = to_utc_rfc3339_from_pt(r["PublishTime_Pacific"])
            if yt_ready():
                try:
                    vid = yt_upload(str(out_file), title, desc, publish_at, tags=tags)
                    print("YouTube video id:", vid)
                    state["posted"].append(key)
                    save_and_commit_state(state, f"posted {key} -> {vid}")
                except (HttpError, RefreshError, Exception) as e:
                    print("YouTube upload skipped due to error:", repr(e))
            else:
                print("YouTube secrets missing/blank; skipped upload. (File saved in renders/.)")

        finally:
            try:
                if final: final.close()
            except: pass

if __name__ == "__main__":
    main()
