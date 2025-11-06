# make_videos.py — safe starter
import csv, sys
from pathlib import Path

CSV = Path("prompts.csv")
if not CSV.exists():
    print("No prompts.csv found yet — that's okay for this dry run.")
    sys.exit(0)  # success, do not fail

rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
print(f"Found {len(rows)} rows.")
for r in rows[:3]:
    print("Sample row:", {k: r.get(k) for k in list(r.keys())[:6]})
