import os
"""Assemble frame-checked ground truth vs. Cosmos Reason outputs into one JSON for W&B."""
import json
import re

VZ = "/home/jayakumaronline/vast-builders-challenge/tools/vision-zero"
reviews = json.load(open(f"{VZ}/reviews.json"))
scores = json.load(open(f"{VZ}/video_scores.json"))

CHECKED_NEGATIVE = f"s3://{os.environ['S3_SEGMENTS_BUCKET']}/segments/20261001_080432_2025_test_Warehouse_017_Camera_01_chunk_0006_segment_002_of_006.mp4"
checked = {**reviews, CHECKED_NEGATIVE: {"verdict": "not_risky", "note": "Only a humanoid robot and an AGV."}}

review_rows = []
for src, r in checked.items():
    f = scores.get(src, {})
    wh = "Warehouse" in src or "seed_" in src
    raw = f.get("risk") or 0
    final = 0 if f.get("humans_near") == 0 else raw
    truth = r["verdict"] == "confirmed"
    review_rows.append({
        "clip": src.split("/")[-1], "domain": "warehouse" if wh else "street",
        "cosmos_risk_raw": raw, "cosmos_risk_final": final, "cosmos_event": f.get("event"),
        "machine": f.get("machine"), "humans_near": f.get("humans_near"), "cosmos_why": f.get("why"),
        "human_verdict": r["verdict"], "reviewer_note": r.get("note", ""), "human_says_risky": truth,
        "raw_agrees": (raw >= 2) == truth, "final_agrees": (final >= 2) == truth})


def flags(text):
    g = lambda k: bool(re.search(k + r":\s*<?\s*yes", text, re.I))
    why = re.search(r"WHY:\s*<?(.*?)>?\s*$", text, re.M)
    return g("BOMB_RELEASE") or g("BOMB_FALLING"), g("IMPACT"), (why.group(1) if why else "")


# Film 1: Disney Bomb 1945 (all 16 clips frame-checked)
f1 = json.load(open("/home/jayakumaronline/vision-zero-work/wm/prompt_bomb.txt.results.json"))
truth1 = {8: "bomb_drop", 9: "bomb_drop", 10: "bomb_drop", 12: "bomb_drop", 13: "bomb_drop",
          1: "bomb_loading", 2: "bomb_loading", 3: "bomb_loading", 4: "bomb_loading"}
# Film 2: Special Delivery (41 flagged + 20 random unflagged clips frame-checked)
f2 = json.load(open("/home/jayakumaronline/vision-zero-work/wm2/prompt_bomb.txt.results.json"))
pos = json.load(open("/home/jayakumaronline/vision-zero-work/wm2/pos.json"))
neg = json.load(open("/home/jayakumaronline/vision-zero-work/wm2/negsamp.json"))
truth2 = {i: "explosion" for i in [58, 59, 109, 110, 111, 112] + list(range(113, 127))}
truth2.update({i: "parachute_or_cargo_drop" for i in [3, 14, 15, 17, 18, 19, 23, 24, 25]})
truth2.update({96: "bomb_drop", 55: "bomb_drop"})

film_rows = []
for film, res, truth, ids, pat in [
        ("Disney Bomb 1945", f1, truth1, range(16), "seg_{:02d}.mp4"),
        ("Special Delivery", f2, truth2, sorted(pos + neg), "seg_{:03d}.mp4")]:
    for i in ids:
        rel, imp, why = flags(res[pat.format(i)])
        t = truth.get(i, "none")
        film_rows.append({"film": film, "clip": i, "t_start_s": i * 5, "truth": t,
                          "cosmos_release": rel, "cosmos_impact": imp, "cosmos_why": why,
                          "release_ok": rel == (t == "bomb_drop"), "impact_ok": imp == (t == "explosion")})


def prf(rows, pred, pos_label):
    tp = sum(1 for r in rows if r[pred] and r["truth"] == pos_label)
    fp = sum(1 for r in rows if r[pred] and r["truth"] != pos_label)
    fn = sum(1 for r in rows if not r[pred] and r["truth"] == pos_label)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


summary = {}
for d in ("street", "warehouse"):
    rows = [r for r in review_rows if r["domain"] == d]
    summary[f"{d}/checked"] = len(rows)
    summary[f"{d}/agreement_raw"] = round(sum(r["raw_agrees"] for r in rows) / len(rows), 3)
    summary[f"{d}/agreement_final"] = round(sum(r["final_agrees"] for r in rows) / len(rows), 3)
for film in ("Disney Bomb 1945", "Special Delivery"):
    rows = [r for r in film_rows if r["film"] == film]
    for k, v in prf(rows, "cosmos_release", "bomb_drop").items():
        summary[f"{film}/bomb_release_{k}"] = v
    for k, v in prf(rows, "cosmos_impact", "explosion").items():
        summary[f"{film}/explosion_{k}"] = v
json.dump({"reviews": review_rows, "films": film_rows, "summary": summary}, open("/home/jayakumaronline/vision-zero-work/wm/eval.json", "w"), indent=1)
print(json.dumps(summary, indent=1))
