"""Ground truth (graded by eye from contact sheets) vs Cosmos Reason v1/v2 for 5 archival films."""
import json, sys
sys.path.insert(0, "/home/jayakumaronline/vision-zero-work/wm3")
from metrics import flags, prf

# truth label, optional per-clip overrides for the two binary targets, note.
# truth_release / truth_explosion: True / False / None (None = uncertain, excluded from that metric).
T = {}

def lab(film, clip, truth, note="", rel=None, exp=None):
    tr = truth == "bomb_drop"
    te = {"explosion": True}.get(truth, False if truth != "uncertain" else None)
    if rel is not None: tr = rel
    if exp is not None: te = exp
    if rel == "u": tr = None
    if exp == "u": te = None
    T[(film, clip)] = dict(truth=truth, truth_release=tr, truth_explosion=te, note=note)

B17, TIR, RAF = "b17", "tirpitz", "rafnight"
# --- USAAF B-17 Flying Fortresses bombing raids (Movietone, 1943)
for c in [1, 3, 4, 5, 20, 21, 24, 25, 29, 31, 58]: lab(B17, c, "none", "ground/crew/title")
for c in [6, 16, 32, 33, 34, 36, 44, 46, 49, 51]: lab(B17, c, "none", "aircraft flying/taking off/landing or clouds; no bomb visible")
lab(B17, 8, "none", "aerial view of harbour target, no bombs or bursts visible")
lab(B17, 9, "bomb_drop", "bombs fall past camera over smoke of bomb bursts", exp=True)
for c in [10, 11, 12, 13, 14]: lab(B17, c, "explosion", "aerial view of bomb-burst smoke columns over target")
lab(B17, 37, "bomb_drop", "stick of finned bombs falling from bomb bay over farmland")
lab(B17, 40, "uncertain", "aerial view of town; possible small white bursts late in clip")
lab(B17, 41, "uncertain", "aerial view; possible smoke puff at left in first frames")
lab(B17, 42, "explosion", "aerial view; clusters of bomb-burst smoke appear late in clip (small, moderate confidence)")
lab(B17, 62, "uncertain", "dark specks in grey sky: flak bursts or distant aircraft")

# --- 1944-11-22 RAF Sinks Tirpitz (Universal Newsreels; multi-story newsreel)
lab(TIR, 1, "none", "Lancasters in flight")
lab(TIR, 2, "none", "aerial view of fjord target, no bomb visible")
lab(TIR, 3, "none", "distant aircraft, haze; smoke screen at end")
lab(TIR, 4, "uncertain", "smoke screen over fjord, possible flak flash")
for c in [5, 7, 8, 9, 10, 11, 12, 13, 14]: lab(TIR, c, "explosion", "bomb burst clouds at Tirpitz anchorage seen from aircraft")
lab(TIR, 6, "explosion", "bomb bursts; small dark objects in sky may be bombs or aircraft", rel="u")
lab(TIR, 19, "none", "artillery crew with shells (not aircraft bombs)")
lab(TIR, 22, "explosion", "artillery shell burst in tree line")
lab(TIR, 23, "uncertain", "smoke drifting behind farmhouse, no discrete burst")
lab(TIR, 24, "explosion", "artillery hit flash on church steeple")
lab(TIR, 25, "explosion", "howitzer firing then smoke burst at steeple (moderate confidence)")
lab(TIR, 26, "uncertain", "steeple burning (fire, not a visible blast)")
for c in [28, 29, 31, 33]: lab(TIR, c, "none", "AA guns firing at ground targets / night gun flashes")
lab(TIR, 30, "none", "gun smoke drifting across field, soldier walking")
lab(TIR, 32, "uncertain", "AA gun muzzle smoke; night flash at end may be a shell burst")
lab(TIR, 34, "uncertain", "night tracers/flashes")
for c in [35, 38, 39, 66, 72, 76, 78, 79, 80, 82, 85]: lab(TIR, c, "none", "bond-drive parade, Denmark street scenes, football, title cards, boat")
for c in [50, 51]: lab(TIR, c, "uncertain", "Denmark: large building fire with smoke (sabotage); no discrete blast seen")
lab(TIR, 65, "uncertain", "Denmark: smashed shop windows, then smoke over street")
lab(TIR, 69, "uncertain", "Denmark: dark smoke burst by building then burnt-out facade")

# --- Colorized RAF night bombing of German cities (RAF; colorized compilation)
# Rule: discrete bright bloom/flash = explosion (may be a 4000 lb blast or a photoflash);
# burning city/incendiary carpets without a discrete bloom = uncertain (fire); dark/specks/titles = none.
for c in [2, 5, 8, 9, 10, 35, 36, 38, 39, 41, 60, 89, 92]: lab(RAF, c, "none", "dark frame, faint specks or title card")
for c in [11, 12, 14, 15, 16, 18, 19, 20, 21, 27, 30, 44, 46, 53, 77, 78]: lab(RAF, c, "explosion", "night: discrete bright blast bloom over target")
for c in [6, 17, 23, 24, 25, 26, 28, 31, 32, 40, 42, 43, 45, 47, 48, 49, 50, 51, 52, 55, 57, 58, 62, 63,
          65, 66, 67, 68, 69, 70, 71, 75, 76, 79, 80, 81, 82, 83, 84, 85, 86]:
    lab(RAF, c, "uncertain", "night: ground fires / incendiaries / single moving light; no discrete blast")

FILMS = {B17: "USAAF B-17 Flying Fortresses bombing raids", TIR: "1944-11-22 RAF Sinks Tirpitz",
         RAF: "Colorized film footage of UK RAF night bombing of German cities (1943-1945)"}

rows = []

def add(film, clip, t, tr, te, res_text, pv, note=""):
    rel, imp, why = flags(res_text)
    rows.append(dict(film=film, clip=clip, t_start_s=clip * 5, truth=t, truth_release=tr, truth_explosion=te,
                     cosmos_release=rel, cosmos_impact=imp, cosmos_why=why, prompt_version=pv,
                     release_ok=None if tr is None else rel == tr, impact_ok=None if te is None else imp == te,
                     grader_note=note))

plan = json.load(open("/home/jayakumaronline/vision-zero-work/wm3/grade_plan.json"))
for short, title in FILMS.items():
    missing = set(plan[short]["grade"]) - {c for f, c in T if f == short}
    assert not missing, (short, missing)
    for pv in ("v2", "v1"):
        res = json.load(open(f"/home/jayakumaronline/vision-zero-work/wm3/{short}/prompt_bomb_{pv}.txt.results.json"))
        for c in plan[short]["grade"]:
            g = T[(short, c)]
            add(title, c, g["truth"], g["truth_release"], g["truth_explosion"], res["seg_%03d.mp4" % c], pv, g["note"])

# Films 1-2 (truth from earlier grading)
truth1 = {8: "bomb_drop", 9: "bomb_drop", 10: "bomb_drop", 12: "bomb_drop", 13: "bomb_drop",
          1: "bomb_loading", 2: "bomb_loading", 3: "bomb_loading", 4: "bomb_loading"}
truth2 = {i: "explosion" for i in [58, 59] + list(range(109, 127))}
truth2.update({i: "parachute_or_cargo_drop" for i in [3, 14, 15, 17, 18, 19, 23, 24, 25]})
truth2.update({96: "bomb_drop", 55: "bomb_drop"})
ids2 = sorted(json.load(open("/home/jayakumaronline/vision-zero-work/wm2/pos.json")) + json.load(open("/home/jayakumaronline/vision-zero-work/wm2/negsamp.json")))
for pv, f in (("v2", "prompt_bomb_v2.txt"), ("v1", "prompt_bomb.txt")):
    r1 = json.load(open(f"/home/jayakumaronline/vision-zero-work/wm/{f}.results.json"))
    r2 = json.load(open(f"/home/jayakumaronline/vision-zero-work/wm2/{f}.results.json"))
    for i in range(16):
        t = truth1.get(i, "none")
        add("Disney Bomb 1945", i, t, t == "bomb_drop", t == "explosion", r1["seg_%02d.mp4" % i], pv)
    for i in ids2:
        t = truth2.get(i, "none")
        add("U.S. Army Air Forces - Special Delivery", i, t, t == "bomb_drop", t == "explosion", r2["seg_%03d.mp4" % i], pv)

json.dump(rows, open("/home/jayakumaronline/vision-zero-work/wm3/eval_films.json", "w"), indent=1)


def prf2(rs, pred, truth):
    rs = [r for r in rs if r[truth] is not None]
    tp = sum(r[pred] and r[truth] for r in rs); fp = sum(r[pred] and not r[truth] for r in rs)
    fn = sum((not r[pred]) and r[truth] for r in rs)
    return dict(n=len(rs), tp=tp, fp=fp, fn=fn, precision=round(tp / (tp + fp), 3) if tp + fp else None,
                recall=round(tp / (tp + fn), 3) if tp + fn else None)


summary = {}
films = ["Disney Bomb 1945", "U.S. Army Air Forces - Special Delivery"] + list(FILMS.values())
for film in films + ["ALL"]:
    for pv in ("v1", "v2"):
        rs = [r for r in rows if r["prompt_version"] == pv and (film == "ALL" or r["film"] == film)]
        summary[f"{film} | {pv}"] = dict(graded=len(rs), flagged_release=sum(r["cosmos_release"] for r in rs),
                                         flagged_impact=sum(r["cosmos_impact"] for r in rs),
                                         release=prf2(rs, "cosmos_release", "truth_release"),
                                         explosion=prf2(rs, "cosmos_impact", "truth_explosion"))
json.dump(summary, open("/home/jayakumaronline/vision-zero-work/wm3/metrics_summary.json", "w"), indent=1)
for k, v in summary.items():
    print(f"{k:85s} graded={v['graded']:3d} | REL n={v['release']['n']} tp={v['release']['tp']} fp={v['release']['fp']} fn={v['release']['fn']} P={v['release']['precision']} R={v['release']['recall']} | EXP n={v['explosion']['n']} tp={v['explosion']['tp']} fp={v['explosion']['fp']} fn={v['explosion']['fn']} P={v['explosion']['precision']} R={v['explosion']['recall']}")
