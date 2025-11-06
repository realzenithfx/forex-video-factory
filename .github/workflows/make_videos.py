# make_videos.py — Forex Voyage Shorts factory
# Reads prompts.csv, builds 9:16 videos with Pexels b-roll + Audio Library music,
# uploads to YouTube as PRIVATE with status.publishAt for scheduled publishing.

import os, csv, json, random, math, time, subprocess
from pathlib import Path
from datetime import datetime, timedelta

import requests
import pytz
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    VideoFileClip, ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_videoclips, ColorClip
)
from moviepy.video.fx.all import crop, resize
from moviepy.audio.fx.all import volumex

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- Config ---
CSV_PATH = Path("prompts.csv")
MUSIC_DIR = Path("music")
OUT_DIR = Path("renders")
TMP_DIR = Path("tmp")
STATE_FILE = Path("posted_state.json")

PUBLISH_WINDOW_MIN = 60  # schedule rows whose PublishTime_Pacific is within next hour
TARGET_DURATION = 32      # seconds (final short length)
W, H = 1080, 1920         # 9:16

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# --- Helpers ---
def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posted": []}

def save_and_commit_state(state, message="update posting state"):
    STATE_FILE.write_text(json.dumps(state, indent=2))
    # Commit so state persists across runs
    try:
        subprocess.run(["git", "config", "user.email", "bot@github.actions"],
                       check=True)
        subprocess.run(["git", "config", "user.name", "gh-actions-bot"],
                       check=True)
        subprocess.run(["git", "add", str(STATE_FILE)], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print("State commit skipped:", e)

def pacific_now():
    tz = pytz.timezone("America/Los_Angeles")
    return datetime.now(tz)

def to_utc_rfc3339_from_pacific_str(s):
    # s like "YYYY-MM-DD HH:MM"
    tz = pytz.timezone("America/Los_Angeles")
    dt_naive = datetime.strptime(s, "%Y-%m-%d %H:%M")
    dt_local = tz.localize(dt_naive)
    dt_utc = dt_local.astimezone(pytz.utc)
    return dt_utc.isoformat()

def rows_due_this_hour(rows):
    """Return rows whose PublishTime_Pacific is within [now, now+PUBLISH_WINDOW_MIN)."""
    now_pt = pacific_now()
    end_pt = now_pt + timedelta(minutes=PUBLISH_WINDOW_MIN)
    due = []
    for r in rows:
        try:
            dt = pytz.timezone("America/Los_Angeles").localize(
                datetime.strptime(r["PublishTime_Pacific"], "%Y-%m-%d %H:%M")
            )
            if now_pt <= dt < end_pt:
                due.append(r)
        except Exception as e:
            print("Bad date row skipped:", r.get("PublishTime_Pacific"), e)
    return due

def pick_music():
    if not MUSIC_DIR.exists():
        return None
    tracks = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a"}]
    return random.choice(tracks) if tracks else None

# ---------- Pexels ----------
def pexels_get_vertical_video(keyword):
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": keyword, "per_page": 5, "orientation": "portrait"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    for v in data.get("videos", []):
        # pick the best portrait file
        files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 0))
        for f in files:
            w, h = f.get("width"), f.get("height")
            link = f.get("link")
            if w and h and h > w and link:
                return link
    return None

def pexels_get_photos(keywords, need=5):
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": keywords, "per_page": max(need, 5), "orientation": "portrait"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    out = []
    for p in data.get("photos", []):
        src = p.get("src", {})
        link = src.get("large") or src.get("portrait") or src.get("large2x")
        if link:
            out.append(link)
    return out[:need]

def download(url, dest: Path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

# ---------- Visual assembly ----------
def render_text_image(text, width, height, font_size=70, fill=(255,255,255), bg=(11,31,59,200)):
    # Semi-transparent bar with centered text
    img = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    # draw bar
    draw.rectangle([0,0,width,height], fill=bg)
    # load a default font (Pillow fallback)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    # wrap text
    lines = []
    words = text.split()
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        tw, th = draw.textsize(test, font=font)
        if tw > width - 80:
            lines.append(line)
            line = w
        else:
            line = test
    if line:
        lines.append(line)
    y = (height - len(lines)*font_size*1.1)//2
    for ln in lines:
        tw, th = draw.textsize(ln, font=font)
        x = (width - tw)//2
        draw.text((x,y), ln, font=font, fill=fill)
        y += int(font_size*1.1)
    p = TMP_DIR / f"text_{int(time.time()*1000)}.png"
    img.save(p)
    return str(p)

def build_from_video(vpath, overlay_text):
    clip = VideoFileClip(vpath)
    # center-crop to 9:16 if needed
    w, h = clip.w, clip.h
    target_ratio = W / H
    ratio = w / h
    if abs(ratio - target_ratio) > 0.01:
        # crop width for portrait
        new_w = int(h * target_ratio)
        x1 = max(0, (w - new_w)//2)
        clip = crop(clip, x1=x1, y1=0, x2=x1+new_w, y2=h)
    clip = clip.resize((W, H))
    if clip.duration > TARGET_DURATION:
        clip = clip.subclip(0, TARGET_DURATION)
    else:
        # loop to reach target length
        loops = math.ceil(TARGET_DURATION / clip.duration)
        clip = concatenate_videoclips([clip] * loops).subclip(0, TARGET_DURATION)

    # Overlay bar at top
    bar_h = 200
    bar = ColorClip(size=(W, bar_h), color=(11,31,59)).set_opacity(0.7).set_duration(TARGET_DURATION).set_position(("center", "top"))
    txt_img = render_text_image(overlay_text, W, bar_h, font_size=64)
    txt = ImageClip(txt_img).set_duration(TARGET_DURATION).set_position(("center","top"))
    return CompositeVideoClip([clip, bar, txt])

def build_from_photos(paths, overlay_text):
    # create simple slideshow
    per = max(5, int(TARGET_DURATION / max(1, len(paths))))
    clips = []
    for p in paths:
        ic = ImageClip(p).resize(height=H).resize(width=W).set_duration(per)
        clips.append(ic)
    # ensure we hit target duration
    video = concatenate_videoclips(clips).subclip(0, TARGET_DURATION)
    bar_h = 200
    bar = ColorClip(size=(W, bar_h), color=(11,31,59)).set_opacity(0.7).set_duration(TARGET_DURATION).set_position(("center", "top"))
    txt_img = render_text_image(overlay_text, W, bar_h, font_size=64)
    txt = ImageClip(txt_img).set_duration(TARGET_DURATION).set_position(("center","top"))
    return CompositeVideoClip([video, bar, txt])

# ---------- YouTube ----------
def youtube_service():
    creds = Credentials(
        None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds)

def youtube_upload_and_schedule(file_path, title, description, publish_at_rfc3339, tags=None):
    yt = youtube_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "27",  # Education
            "tags": tags or [],
        },
        "status": {
            "privacyStatus": "private",      # must be PRIVATE for publishAt to work
            "publishAt": publish_at_rfc3339, # RFC3339 UTC
            "selfDeclaredMadeForKids": False
        }
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress() * 100)}%")
    vid = response.get("id")
    print("YouTube video id:", vid)
    return vid

# ---------- Main ----------
def main():
    ensure_dirs()

    if not CSV_PATH.exists():
        print("prompts.csv not found. Add it to the repo root.")
        return

    # Load state
    state = load_state()

    # Read CSV
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    due = rows_due_this_hour(rows)
    print(f"Due this hour (Pacific): {len(due)} row(s)")

    if not due:
        return

    for r in due:
        # dedupe key
        key = f"{r['PublishTime_Pacific']}|{r['Title']}"
        if key in state["posted"]:
            print("Already posted, skipping:", key)
            continue

        title = r.get("Title", "Forex Voyage")
        overlay = r.get("OverlayText") or title
        script = r.get("Prompt_or_Script", "")
        hashtags = r.get("Hashtags", "")
        cta = r.get("CTA", "Education only. This promotes my company, ZenithFX.")
        keywords = r.get("Broll_Keywords", "forex charts;world map").split(";")
        keyword = keywords[0]

        # 1) Try Pexels video
        vurl = None
        try:
            vurl = pexels_get_vertical_video(keyword)
        except Exception as e:
            print("Pexels video search failed:", e)

        clip_path = None
        final = None
        try:
            if vurl:
                clip_path = TMP_DIR / f"pexels_{int(time.time())}.mp4"
                download(vurl, clip_path)
                final = build_from_video(str(clip_path), overlay)
            else:
                # 2) fallback: photos slideshow
                photos = []
                for kw in keywords:
                    try:
                        photos += pexels_get_photos(kw, need=3)
                    except Exception as e:
                        print("Pexels photos failed for", kw, e)
                if not photos:
                    print("No media found; skipping row:", key)
                    continue
                photo_paths = []
                for i, url in enumerate(photos[:6]):
                    p = TMP_DIR / f"ph_{i}_{int(time.time())}.jpg"
                    download(url, p)
                    photo_paths.append(str(p))
                final = build_from_photos(photo_paths, overlay)

            # audio
            music = pick_music()
            if music:
                a = AudioFileClip(str(music))
                a = volumex(a, 0.18)  # gentle background
                final = final.set_audio(a)

            # export mp4
            safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in title)[:50]
            out_file = OUT_DIR / f"{safe_name}_{r['PublishTime_Pacific'].replace(' ','_').replace(':','-')}.mp4"
            final.write_videofile(str(out_file), fps=30, codec="libx264", audio_codec="aac", threads=4, preset="medium")

            # build description
            desc_lines = [
                script.strip(),
                "",
                "Education only — not financial advice.",
                "This video promotes my company, ZenithFX.",
                "",
                r.get("CTA","").strip(),
            ]
            link = r.get("ZenithFX_Link") or "https://zenithfx.com/"
            desc_lines.append(link)
            if hashtags:
                desc_lines.append(hashtags)
            description = "\n".join([d for d in desc_lines if d])

            # schedule
            publish_at = to_utc_rfc3339_from_pacific_str(r["PublishTime_Pacific"])
            tags = [t.strip("# ") for t in hashtags.split() if t.startswith("#")]

            vid = youtube_upload_and_schedule(
                str(out_file), title, description, publish_at, tags=tags
            )

            # record state
            state["posted"].append(key)
            save_and_commit_state(state, message=f"mark posted {key} -> {vid}")

            print("DONE:", key, "->", vid)

        finally:
            try:
                if final:
                    final.close()
            except:
                pass

if __name__ == "__main__":
    main()
