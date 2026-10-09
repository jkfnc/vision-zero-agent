"""Assemble ~/vision-zero-work/public: the shareable demo assets plus the war-footage review set.

Everything is symlinked or small derived files, so re-running is cheap. Only films we are
comfortable showing get clips; the B-17 (AP watermark) and colorized RAF films stay stats-only.
"""
import json
import os
import shutil
import subprocess

W = os.path.expanduser("~/vision-zero-work")
PUB = os.path.join(W, "public")
DEMO = os.path.join(W, "demo")

SHOWN = {
    "Disney Bomb 1945": ("disney", os.path.join(W, "wm"), 2),
    "U.S. Army Air Forces - Special Delivery": ("special_delivery", os.path.join(W, "wm2"), 3),
    "1944-11-22 RAF Sinks Tirpitz": ("tirpitz", os.path.join(W, "wm3/tirpitz"), 3),
}


def link(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        os.remove(dst)
    os.symlink(src, dst)


def build_demo():
    d = os.path.join(PUB, "demo")
    os.makedirs(os.path.join(d, "audio"), exist_ok=True)
    link(os.path.join(DEMO, "vision_zero_demo.mp4"), os.path.join(d, "vision_zero_demo.mp4"))
    link(os.path.join(DEMO, "vision_zero_deck.pdf"), os.path.join(d, "vision_zero_deck.pdf"))
    link(os.path.join(DEMO, "clips/war_short.mp4"), os.path.join(d, "war_reel.mp4"))
    link(os.path.join(DEMO, "shots"), os.path.join(PUB, "shots"))
    link(os.path.join(DEMO, "slides/deck.html"), os.path.join(PUB, "slides/deck.html"))
    narration = json.load(open(os.path.join(DEMO, "narration.json")))
    items = []
    for n in narration:
        wav = os.path.join(DEMO, "audio", n["id"] + ".norm.wav")
        mp3 = os.path.join(d, "audio", n["id"] + ".mp3")
        if os.path.exists(wav) and (not os.path.exists(mp3) or os.path.getmtime(mp3) < os.path.getmtime(wav)):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-b:a", "96k", mp3], check=True)
        items.append({"id": n["id"], "text": n["text"], "mp3": f"audio/{n['id']}.mp3"})
    full = os.path.join(d, "audio", "narration_full.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(DEMO, "vision_zero_demo.mp4"),
                    "-vn", "-b:a", "128k", full], check=True)
    json.dump(items, open(os.path.join(d, "narration.json"), "w"), indent=1)

    rows = "\n".join(f'<li><b>{i["id"]}</b> <audio controls preload="none" src="{i["mp3"]}"></audio>'
                     f'<div class="t">{i["text"]}</div></li>' for i in items)
    html = f"""<!doctype html><meta charset="utf-8"><title>Vision Zero Agent · demo assets</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;background:#0d1117;color:#e6edf3;max-width:980px;margin:24px auto;padding:0 16px}}
a{{color:#58a6ff}}video{{width:100%;border-radius:8px;background:#000}}li{{margin:10px 0}}.t{{color:#9da7b3;font-size:13px}}
audio{{height:30px;vertical-align:middle;margin-left:8px}}</style>
<h1>Vision Zero Agent · team-4 demo</h1>
<p><a href="vision_zero_demo.mp4" download>Download video (MP4)</a> ·
<a href="vision_zero_deck.pdf">Slides (PDF)</a> · <a href="../slides/deck.html">Slides (HTML)</a> ·
<a href="audio/narration_full.mp3" download>Narration (MP3)</a> · <a href="../../war">War-footage agent</a></p>
<video controls preload="metadata" src="vision_zero_demo.mp4"></video>
<h2>Narration by scene</h2><p class="t">Voiced with Piper TTS (en_US-lessac-medium, research/non-commercial voice).</p>
<ol style="list-style:none;padding:0">{rows}</ol>"""
    open(os.path.join(d, "index.html"), "w").write(html)


def build_war():
    d = os.path.join(PUB, "war")
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    rows = json.load(open(os.path.join(W, "wm3/eval_films.json")))
    clips = {}
    for r in rows:
        if r["film"] not in SHOWN:
            continue
        short, src_dir, width = SHOWN[r["film"]]
        key = (short, r["clip"])
        name = f"seg_{r['clip']:0{width}d}"
        c = clips.setdefault(key, {
            "film": r["film"], "film_id": short, "clip": r["clip"], "t_start_s": r["t_start_s"],
            "video": f"war/{short}/{name}.mp4", "thumb": f"war/{short}/{name}.jpg",
            "truth": r["truth"], "truth_release": r["truth_release"], "truth_explosion": r["truth_explosion"],
            "grader_note": r.get("grader_note"), "cosmos": {}})
        c["cosmos"][r["prompt_version"]] = {k: r[k] for k in
                                            ("cosmos_release", "cosmos_impact", "cosmos_why", "release_ok", "impact_ok")}
        mp4 = os.path.join(src_dir, name + ".mp4")
        link(mp4, os.path.join(d, short, name + ".mp4"))
        jpg = os.path.join(d, short, name + ".jpg")
        if not os.path.exists(jpg):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "2.5", "-i", mp4, "-frames:v", "1",
                            "-vf", "scale=360:-2", jpg], check=True)
    attribution = json.load(open(os.path.join(W, "wm3/attribution.json")))
    for a in attribution:
        a.pop("local_dir", None)
        a["shown_in_ui"] = any(a.get("title", "").startswith(f) for f in SHOWN)
    out = {"clips": sorted(clips.values(), key=lambda c: (c["film_id"], c["clip"])),
           "metrics": json.load(open(os.path.join(W, "wm3/metrics_summary.json"))),
           "attribution": attribution,
           "prompt_v2": open(os.path.join(W, "wm3/prompt_bomb_v2.txt")).read()}
    json.dump(out, open(os.path.join(d, "index.json"), "w"), indent=1)
    link(os.path.join(DEMO, "clips/war_short.mp4"), os.path.join(d, "war_reel.mp4"))
    print(f"war: {len(out['clips'])} clips from {len(SHOWN)} films")


if __name__ == "__main__":
    os.makedirs(PUB, exist_ok=True)
    build_demo()
    build_war()
    print("public ->", PUB)
