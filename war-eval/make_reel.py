import json, os, subprocess

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
W = "/home/jayakumaronline/vision-zero-work/wm3/reel_parts"
os.makedirs(W, exist_ok=True)

# (film caption, source, clip index, offset within clip s, duration s, cosmos verdict, human check, false alarm?)
REEL = [
    ("Disney Bomb (1945)", "/home/jayakumaronline/vision-zero-work/wm/disney.ogv", 9, 0.0, 4.0, "BOMB RELEASE", "correct", False),
    ("U.S. Army Air Forces - Special Delivery", "/home/jayakumaronline/vision-zero-work/wm2/sd.webm", 96, 1.5, 3.5, "BOMB RELEASE", "correct", False),
    ("USAAF B-17 Flying Fortresses bombing raids", "/home/jayakumaronline/vision-zero-work/wm3/b17/src.webm", 37, 1.3, 3.7, "BOMB RELEASE", "correct", False),
    ("USAAF B-17 Flying Fortresses bombing raids", "/home/jayakumaronline/vision-zero-work/wm3/b17/src.webm", 9, 0.5, 4.0, "BOMB RELEASE + EXPLOSION", "correct", False),
    ("USAAF B-17 Flying Fortresses bombing raids", "/home/jayakumaronline/vision-zero-work/wm3/b17/src.webm", 12, 0.0, 3.5, "EXPLOSION", "correct", False),
    ("RAF Sinks Tirpitz (Universal Newsreel, 1944)", "/home/jayakumaronline/vision-zero-work/wm3/tirpitz/src.ogv", 12, 0.0, 4.0, "EXPLOSION", "correct", False),
    ("RAF night bombing of German cities (colorized)", "/home/jayakumaronline/vision-zero-work/wm3/rafnight/src.webm", 15, 0.3, 4.0, "EXPLOSION", "correct", False),
    ("U.S. Army Air Forces - Special Delivery", "/home/jayakumaronline/vision-zero-work/wm2/sd.webm", 116, 0.0, 4.0, "EXPLOSION", "correct", False),
    ("U.S. Army Air Forces - Special Delivery", "/home/jayakumaronline/vision-zero-work/wm2/sd.webm", 24, 0.0, 5.0, "BOMB RELEASE",
     "FALSE ALARM (supply parachutes)", True),
]

parts, manifest = [], []
for k, (title, src, clip, off, dur, verdict, check, fa) in enumerate(REEL):
    t = clip * 5 + off
    l1 = f"{title}  {int(t)//60:02d}:{int(t)%60:02d}"
    l2 = f"Cosmos: {verdict}  |  Human check: {check}"
    f1, f2 = f"{W}/l1_{k}.txt", f"{W}/l2_{k}.txt"
    open(f1, "w").write(l1); open(f2, "w").write(l2)
    c2 = "0xff5555" if fa else "white"
    box = "box=1:boxcolor=black@0.55:boxborderw=10"
    vf = ("scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
          f"drawtext=fontfile={FONT}:textfile={f1}:fontsize=28:fontcolor=white:{box}:x=30:y=h-118,"
          f"drawtext=fontfile={FONT}:textfile={f2}:fontsize=28:fontcolor={c2}:{box}:x=30:y=h-64")
    out = f"{W}/part_{k}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", src, "-t", f"{dur}", "-an", "-vf", vf,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", out], check=True)
    parts.append(out)
    manifest.append(dict(order=k + 1, film=title, source=src, clip=clip, film_timestamp=f"{int(t)//60:02d}:{int(t)%60:02d}",
                         t_start_s=round(t, 2), duration_s=dur, cosmos_verdict=verdict, human_check=check, false_alarm=fa))

open(f"{W}/list.txt", "w").write("".join(f"file '{p}'\n" for p in parts))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{W}/list.txt", "-c", "copy",
                "-movflags", "+faststart", "/home/jayakumaronline/vision-zero-work/wm3/war_highlights.mp4"], check=True)
json.dump(manifest, open("/home/jayakumaronline/vision-zero-work/wm3/war_highlights_manifest.json", "w"), indent=1)
print(json.dumps(manifest, indent=1))
