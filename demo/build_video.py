"""Assemble the demo video: screenshots/slides + real clips overlaid + burned captions + TTS audio."""
import json
import os
import re
import subprocess
import sys

D = "/home/jayakumaronline/vision-zero-work/demo"
S, C, A = f"{D}/shots", f"{D}/clips", f"{D}/audio"
OUT = f"{D}/out"
os.makedirs(OUT, exist_ok=True)
narr = {n["id"]: n["text"] for n in json.load(open(f"{D}/narration.json"))}
dur_path = f"{A}/durations.json"
durs = json.load(open(dur_path)) if os.path.exists(dur_path) else {}
WAR = "/home/jayakumaronline/vision-zero-work/demo/clips/war_short.mp4"

# scene id -> list of (image, share_of_duration, [(clip, x, y, w, h)])
CARD_A, CARD_B = (38, 130, 369, 207), (421, 130, 369, 207)
RULE_A, RULE_B = (38, 108, 369, 207), (421, 108, 369, 207)
SCENES = {
    "01_title": [("01_title.png", 1, [])],
    "10_war": [("10_war.png", 0.18, []), ("WAR", 0.82, [])],
    "10b_live": [("10b_live.png", 0.35, []), ("10b_live_app.png", 0.65, [])],
    "02_problem": [("02_problem.png", 1, [])],
    "03_overview": [("03_overview.png", 1, [])],
    "04_warehouse": [("04_warehouse_boxes.png", 1, [("wh_forklift_boxes.mp4", *CARD_A), ("wh_police_boxes.mp4", *CARD_B)])],
    "05_review": [("04_warehouse_boxes.png", 1, [("wh_forklift_boxes.mp4", *CARD_A), ("wh_police_boxes.mp4", *CARD_B)])],
    "06_street": [("06_street_boxes.png", 1, [("st_squeeze_boxes.mp4", *CARD_A), ("st_sidewalk_boxes.mp4", *CARD_B)])],
    "07_rule": [("07_rule_top.png", 0.45, []),
                ("07_rule_feed.png", 0.55, [("wh_forklift_boxes.mp4", *RULE_A), ("wh_ceiling_boxes.mp4", *RULE_B)])],
    "08_report": [("08_report.png", 0.34, []), ("08_report_b.png", 0.33, []), ("08_report_c.png", 0.33, [])],
    "09_wandb": [("09_wandb.png", 1, [])],
    "11_stack": [("11_stack.png", 1, [])],
    "12_close": [("12_close.png", 1, [])],
}
TOP_CAPTIONS = {"10_war"}  # the war reel burns its own verdict captions at the bottom
ZOOM = {"05_review": (20, 95, 800, 450)}  # crop box zoomed to full frame: highlights the reviewer notes


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"ffmpeg failed: {' '.join(cmd)[:300]}\n{r.stderr[-1500:]}")


def ts(t):
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def write_ass(sid, text, dur, path):
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    chunks = []
    for p in parts:
        words = p.split()
        n = max(1, -(-len(p) // 95))
        target, cur = len(p) / n, ""
        for w in words:
            if cur and len(cur) + len(w) > target and n > 1:
                chunks.append(cur.strip())
                cur = ""
                n -= 1
            cur += w + " "
        if cur.strip():
            chunks.append(cur.strip())
    total = sum(len(c) for c in chunks)
    t, lead = 0.25, 0.25
    span = dur - lead - 0.3
    lines = []
    for c in chunks:
        d = span * len(c) / total
        lines.append(f"Dialogue: 0,{ts(t)},{ts(t + d)},Cap,,0,0,0,,{c}")
        t += d
    open(path, "w").write(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Cap,DejaVu Sans,34,&H00FFFFFF,&H00000000,&H40000000,0,3,12,0,{8 if sid in TOP_CAPTIONS else 2},80,80,30\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(lines) + "\n")


def segment(img, dur, overlays, out, zoom=None):
    if img == "WAR":
        run(["ffmpeg", "-v", "error", "-y", "-i", WAR, "-t", f"{dur:.3f}",
             "-vf", "setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
                    "fps=30,tpad=stop_mode=clone:stop_duration=30,format=yuv420p",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])
        return
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-t", f"{dur:.3f}", "-i", f"{S}/{img}"]
    for clip, *_ in overlays:
        cmd += ["-stream_loop", "-1", "-i", f"{C}/{clip}"]
    fc, last = [], "0:v"
    for i, (clip, x, y, w, h) in enumerate(overlays, 1):
        fc.append(f"[{i}:v]scale={w}:{h},setsar=1,setpts=PTS-STARTPTS[c{i}]")
        fc.append(f"[{last}][c{i}]overlay={x}:{y}:shortest=0:eof_action=repeat[o{i}]")
        last = f"o{i}"
    if zoom:
        x, y, w, h = zoom
        fc.append(f"[{last}]crop={w}:{h}:{x}:{y},scale=1280:720[z]")
        last = "z"
    fc.append(f"[{last}]fps=30,format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-t", f"{dur:.3f}", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out]
    run(cmd)


parts = []
for sid, layout in SCENES.items():
    wav = f"{A}/{sid}.norm.wav"
    dur = float(durs.get(sid) or max(4.0, len(narr[sid]) / 14.5)) + 0.5
    pieces = []
    for k, (img, share, ov) in enumerate(layout):
        if img == "WAR" and not os.path.exists(WAR):
            img, ov = "10_war.png", []
        p = f"{OUT}/{sid}_{k}.mp4"
        segment(img, dur * share, ov, p, ZOOM.get(sid))
        pieces.append(p)
    lst = f"{OUT}/{sid}_list.txt"
    open(lst, "w").write("".join(f"file '{p}'\n" for p in pieces))
    silent = f"{OUT}/{sid}_v.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
    ass = f"{OUT}/{sid}.ass"
    write_ass(sid, narr[sid], dur, ass)
    final = f"{OUT}/{sid}.mp4"
    audio = ["-i", wav] if os.path.exists(wav) else ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=mono"]
    run(["ffmpeg", "-v", "error", "-y", "-i", silent, *audio, "-filter_complex",
         f"[0:v]subtitles={ass}[v];[1:a]apad,atrim=0:{dur:.3f},aresample=48000[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1", final])
    parts.append(final)
    print(f"{sid}: {dur:.1f}s{'' if os.path.exists(wav) else ' (no audio yet)'}", flush=True)

lst = f"{OUT}/all.txt"
open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart",
     f"{D}/vision_zero_demo.mp4"])
r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                    f"{D}/vision_zero_demo.mp4"], capture_output=True, text=True)
print("DONE", f"{D}/vision_zero_demo.mp4", r.stdout.strip(), "s")
