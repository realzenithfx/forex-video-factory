# make_videos.py  — starter
import os, sys, datetime, csv
from pathlib import Path

CSV_PATH = Path("prompts.csv")

def main():
    if not CSV_PATH.exists():
        print("No prompts.csv found. Add one and re-run.")
        sys.exit(0)

    # Read the first few rows to prove the pipeline works
    with CSV_PATH.open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    print(f"Found {len(rows)} rows in prompts.csv")
    # Show a tiny sample in the logs
    for r in rows[:3]:
        print("Sample row:", {k: r.get(k) for k in list(r.keys())[:6]})

    # TODO (Step 4): 
    # 1) Pick rows that are 'due now' (by time or 'Status' column)
    # 2) Call Pexels API with your PEXELS_API_KEY to fetch a clip/stills
    # 3) Build a short video with moviepy + your /music tracks
    # 4) Upload to YouTube via YouTube Data API (videos.insert) with publishAt
    #    NOTE: publishAt only works when privacyStatus='private'
    # 5) Mark the row as posted (log or commit a small state file)

if __name__ == "__main__":
    main()
