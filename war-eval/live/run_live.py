import json, os, time, urllib.request
H = os.environ["VZ_APP_URL"].rstrip("/") + "/"
def call(path, body=None):
    req = urllib.request.Request(H + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body else "GET")
    return json.load(urllib.request.urlopen(req, timeout=60))
out = {}
for film, n in (("abomb_1957", 20), ("bikini_underwater", 10), ("ivy_mike", 6)):
    while call("api/war/live")["running"]: time.sleep(3)
    print(film, call("api/war/live/start", {"film": film, "max_segments": n, "realtime": False}), flush=True)
    time.sleep(5)
    while (d := call("api/war/live"))["running"]: time.sleep(3)
    out[film] = {"segments": d["segments"], "alerts": [{k: a[k] for k in ("segment", "at", "why", "delivery")} for a in d["alerts"]], "error": d["error"]}
    print(film, "done:", sum(s["state"] == "explosion" for s in d["segments"]), "/", len(d["segments"]), "flagged, error", d["error"], flush=True)
    json.dump(out, open("live_results.json", "w"), indent=1)
