import glob
import json
import os

import wandb

d = json.load(open("/tmp/evalpush/eval.json"))
run = wandb.init(entity=os.environ["WANDB_TEAM"], project=os.environ["WANDB_PROJECT"],
                 name="cosmos-reason-frame-checked-eval", job_type="eval",
                 config={"vlm": "nvidia/cosmos3-nano-reasoner", "clip_seconds": 5,
                         "ground_truth": "human frame checks of 4-5 frames per clip"})


def table(rows):
    cols = list(rows[0].keys())
    return wandb.Table(columns=cols, data=[[r[c] for c in cols] for r in rows])


run.log({"safety_reviews": table(d["reviews"]),
         "archival_film_bomb_eval": table(d["films"]),
         "frame_sheets": [wandb.Image(p, caption=os.path.basename(p))
                          for p in sorted(glob.glob("/tmp/evalpush/*.jpg"))]})
run.summary.update(d["summary"])
print("RUN_URL", run.url)
run.finish()
