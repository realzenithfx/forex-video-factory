# make_videos.py — Forex Voyage Shorts factory (NEXT-5 scheduler)
# Schedules the next 5 future rows in prompts.csv each run.
# Builds 9:16 videos with Pexels b-roll or photos + Audio Library music.
# Uploads via YouTube Data API: privacy=private, status.publishAt (official scheduling method).

import os, csv, json, random, math, time, subprocess
from pathlib import Path
from datetime import datetime
import pytz
import requests

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (VideoFileClip, ImageClip, AudioFileClip,
                            CompositeVideoClip, concatenate_videoclips, ColorClip)
from moviepy.video.fx.all import crop
from moviepy.audio.fx.all import volumex

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ---------- Config ----------
CSV_PATH = Path("prompts.csv")
MUSIC_DIR = Path("music")
OUT_DIR = Path("renders")
TMP_DIR = Path("tmp")
STATE_FILE = Path("posted_state.json")

TARGET_DURATION = 32  # seconds
W, H = 1080, 1920     # 9:16

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# ---------- Utilities ----------
def ensure_dirs():
    OUT_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)

def pacific_tz():
    return pytz.timezone("America/Los_Angeles")

def utc_now():
    return datetime.now(pytz.utc)

def parse_pt(dt_str):
    # expects "YYYY-MM-DD HH:MM" in Pacific time
    tz = pacific_tz()
    dt_naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    return tz.localize(dt_naive)

def to_utc_rfc3339_from_pt(dt_str):
    return parse_pt(dt_str).astimezone(pytz.utc).isoformat()

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"posted": []}

def save_and_commit_state(state, message):
    STATE_FILE.write_text(json.dumps(state, indent=2))
    try:
        subprocess.run(["git", "config", "user.email", "bot@github.actions"], check=True)
        subprocess.run(["git", "config", "user.name", "gh-actions-bot"], check=True)
        subprocess.run(["git", "add", str(STATE_FILE)], check=True)
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print("State commit skipped:", e)

def pick_music():
    if not MUSIC_DIR.exists():
        return None
    tracks = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a"}]
    return random.choice(tracks) if tracks else None

# ---------- Pexels ----------
def pexels_video(keyword):
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}
    r = requests.get(url, headers=headers, params={"query": keyword, "per_page": 5, "orientation": "portrait"}, timeout=30)
    r.raise_for_status()
    for v in r.json().get("videos", []):
        for f in sorted(v.get("video_files", []), key=lambda x: x.get("width", 0)):
            w, h, link = f.get("width"), f.get("height"), f.get("link")
            if w and h and link and h > w:  # portrait
                return link
    return None

def pexels_photos(q, need=6):
    url = "https://api.pexels.com/v1/search"
    headers = {"Authorization": PEXELS_API_KEY}
    r = requests.get(url, headers=headers, params={"query": q, "per_page": need, "orientation": "portrait"}, timeout=30)
    r.raise_for_status()
    photos = []
    for p in r.json().get("photos", []):
        src = p.get("src", {})
        link = src.get("large") or src.get("portrait") or src.get("large2x")
        if link:
            photos.append(link)
    return photos[:need]

def download(url, dest: Path):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for ch in r.iter_content(8192):
                if ch:
                    f.write(ch)

# ---------- Visuals ----------
def render_text_img(text, w, h, font_size=64, fill=(255,255,255), bg=(11,31,59,200)):
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0,0,w,h], fill=bg)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    # simple wrap
    words, lines, line = text.split(), [], ""
    for w1 in words:
        test = (line + " " + w1).strip()
        if draw.textlength(test, font=font) > w-80:
            lines.append(line)
            line = w1
        else:
            line = test
    if line: lines.append(line)
    y = int((h - len(lines)*font_size*1.1)/2)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = int((w - tw)/2)
        draw.text((x,y), ln, font=font, fill=fill)
        y += int(font_size*1.1)
    p = TMP_DIR / f"t_{int(time.time()*1000)}.png"
    img.save(p)
    return str(p)

def build_from_video(vpath, overlay):
    clip = VideoFileClip(vpath)
    w, h = clip.w, clip.h
    target_ratio = W/H
    if abs(w/h - target_ratio) > 0.01:
        new_w = int(h*target_ratio)
        x1 = max(0, (w-new_w)//2)
        clip = crop(clip, x1=x1, y1=0, x2=x1+new_w, y2=h)
    clip = clip.resize((W,H))
    if clip.duration < TARGET_DURATION:
        loops = math.ceil(TARGET_DURATION/clip.duration)
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

# ---------- YouTube ----------
def yt_service():
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
        "snippet": {"title": title, "description": description, "categoryId": "27", "tags": tags or []},
        "status": {"privacyStatus": "private", "publishAt": publish_at_rfc3339, "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"Uploaded {int(status.progress()*100)}%")
    return resp.get("id")

# ---------- Main ----------
def main():
    ensure_dirs()
    if not CSV_PATH.exists():
        print("prompts.csv not found in repo root.")
        return

    state = load_state()
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8", newline="")))
    now_pt = pacific_tz().fromutc(utc_now().replace(tzinfo=pytz.utc))  # for logging only
    print("Now (Pacific):", now_pt.strftime("%Y-%m-%d %H:%M"))

    # Pick NEXT 5 future rows not yet posted
    future = []
    for r in rows:
        try:
            dt = parse_pt(r["PublishTime_Pacific"])
        except Exception as e:
            print("Bad date in row, skipping:", r.get("PublishTime_Pacific"), e); continue
        key = f"{r['PublishTime_Pacific']}|{r['Title']}"
        if key in state["posted"]: continue
        if dt > pacific_tz().localize(datetime.now()):  # future only
            future.append((dt, r))
    future.sort(key=lambda x: x[0])
    todo = [r for _, r in future[:5]]

    print(f"Scheduling this run: {len(todo)} video(s).")
    if not todo:
        return

    for r in todo:
        key = f"{r['PublishTime_Pacific']}|{r['Title']}"
        title = r.get("Title") or "Forex Voyage"
        overlay = r.get("OverlayText") or title
        script = r.get("Prompt_or_Script") or ""
        hashtags = r.get("Hashtags","")
        tags = [t.strip("# ") for t in hashtags.split() if t.startswith("#")]
        link = r.get("ZenithFX_Link") or "https://zenithfx.com/"
        desc = "\n".join(filter(None, [
            script.strip(),
            "",
            "Education only — not financial advice.",
            "This video promotes my company, ZenithFX.",
            "",
            r.get("CTA","").strip(),
            link,
            hashtags
        ]))

        # media
        keywords = (r.get("Broll_Keywords") or "forex charts;world map").split(";")
        try:
            vurl = pexels_video(keywords[0])
        except Exception as e:
            print("Pexels video search failed:", e); vurl = None

        final = None
        try:
            if vurl:
                vp = TMP_DIR / f"pv_{int(time.time())}.mp4"
                download(vurl, vp)
                final = build_from_video(str(vp), overlay)
            else:
                photos = []
                for kw in keywords:
                    try:
                        photos += pexels_photos(kw, need=3)
                    except Exception as e:
                        print("Pexels photos failed for", kw, e)
                if not photos:
                    print("No media found; skipping:", key); continue
                fps = []
                for i, u in enumerate(photos[:6]):
                    p = TMP_DIR / f"ph_{i}_{int(time.time())}.jpg"
                    download(u, p); fps.append(str(p))
                final = build_from_photos(fps, overlay)

            # audio
            m = pick_music()
            if m:
                a = AudioFileClip(str(m)).fx(volumex, 0.18)
                final = final.set_audio(a)

            # export
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in title)[:50]
            out_file = OUT_DIR / f"{safe}_{r['PublishTime_Pacific'].replace(' ','_').replace(':','-')}.mp4"
            final.write_videofile(str(out_file), fps=30, codec="libx264", audio_codec="aac", preset="medium", threads=4)

            publish_at = to_utc_rfc3339_from_pt(r["PublishTime_Pacific"])
            try:
                vid = yt_upload(str(out_file), title, desc, publish_at, tags=tags)
                print("YouTube video id:", vid)
                state["posted"].append(key)
                save_and_commit_state(state, f"posted {key} -> {vid}")
            except HttpError as e:
                print("YouTube API error:", e)
                if hasattr(e, "content"):
                    print("Details:", e.content)
        finally:
            try:
                if final: final.close()
            except: pass

if __name__ == "__main__":
    main()
