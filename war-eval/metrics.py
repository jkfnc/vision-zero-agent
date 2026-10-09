import json, re
def flags(text):
    g = lambda k: bool(re.search(k + r":\s*<?\s*yes", text, re.I))
    why = re.search(r"WHY:\s*<?(.*?)>?\s*$", text, re.M)
    return g("BOMB_RELEASE") or g("BOMB_FALLING"), g("IMPACT"), (why.group(1) if why else "")
def prf(rows, k, lab):
    tp=sum(1 for r in rows if r[k] and r["truth"]==lab); fp=sum(1 for r in rows if r[k] and r["truth"]!=lab and r["truth"]!="uncertain")
    fn=sum(1 for r in rows if not r[k] and r["truth"]==lab)
    return dict(tp=tp,fp=fp,fn=fn,precision=round(tp/(tp+fp),3) if tp+fp else None,recall=round(tp/(tp+fn),3) if tp+fn else None)
