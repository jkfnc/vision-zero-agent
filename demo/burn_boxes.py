import os
"""Burn VSS YOLO boxes into a clip, matching the app overlay colors (risky people red, vehicles amber)."""
import json
import subprocess
import sys
import urllib.parse
import urllib.request

APP = os.environ["VZ_APP_URL"].rstrip("/")
VEHICLES = {"car", "truck", "bus", "motorcycle", "bicycle", "train"}


def burn(source, src_mp4, out_mp4, risky=True, warehouse=False):
    d = json.load(urllib.request.urlopen(APP + "/api/detections?" + urllib.parse.urlencode({"source": source}), timeout=60))
    fps = d.get("fps") or 30
    frames = {f["frame_index"]: f.get("detections") or [] for f in d.get("frames", [])}
    n = d.get("frame_count") or (max(frames) + 1 if frames else 0)
    filters, last = [], []
    for i in range(n):
        dets = frames.get(i)
        if dets is None:
            dets = last
        last = dets
        for b in dets:
            lab = b["label"]
            if lab != "person" and lab not in VEHICLES and not warehouse:
                continue
            x1, y1, x2, y2 = b["bbox"]
            color = ("0xff4d4f" if risky else "0x7ee08f") if lab == "person" else "0xffb35c" if lab in VEHICLES else "0x4cc2ff"
            t = 6 if lab == "person" and risky else 4
            filters.append(f"drawbox=x={x1}:y={y1}:w={x2 - x1}:h={y2 - y1}:color={color}:t={t}:enable='eq(n,{i})'")
    script = out_mp4 + ".filters"
    open(script, "w").write(",\n".join(filters) or "null")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src_mp4, "-filter_script:v", script, "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps), out_mp4], check=True)
    return len(filters)


if __name__ == "__main__":
    S = f"s3://{os.environ['S3_SEGMENTS_BUCKET']}/segments/"
    C = "/home/jayakumaronline/vision-zero-work/demo/clips/"
    jobs = [
        ("wh_forklift", S + "20261001_074721_0851b7a332077a4fc0e2_01021d3989e38beb3395_run_10_seed_213384163.eye_00.rgb_chunk_0000_segment_002_of_002.mp4", True),
        ("wh_police", S + "20261001_074904_0851b7a332077a4fc0e2_01021d3989e38beb3395_run_10_seed_213384163.eye_04.rgb_chunk_0000_segment_002_of_002.mp4", True),
        ("wh_ceiling", S + "20261001_074538_0851b7a332077a4fc0e2_01021d3989e38beb3395_run_10_seed_213384163.ceiling_01.rgb_chunk_0000_segment_002_of_002.mp4", True),
        ("st_squeeze", S + "20261008_074241_GX010001_chunk_0014_segment_002_of_006.mp4", False),
        ("st_sidewalk", S + "20261008_074357_GX010001_chunk_0017_segment_004_of_006.mp4", False),
    ]
    for name, src, wh in jobs:
        k = burn(src, C + name + ".mp4", C + name + "_boxes.mp4", risky=True, warehouse=wh)
        print(name, k, "boxes", flush=True)
    sys.exit(0)
