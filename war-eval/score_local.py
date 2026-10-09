import base64, json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

import os

URL = os.environ["COSMOS3_REASON_URL"].rstrip("/")
AUTH = {"Authorization": "Bearer " + os.environ["GPU_BEARER_TOKEN"]}
PROMPT = open(sys.argv[1]).read()
model = json.load(urllib.request.urlopen(urllib.request.Request(URL + "/v1/models", headers=AUTH), timeout=20))["data"][0]["id"]


def one(path):
    mp4 = open(path, "rb").read()
    body = {"model": model, "max_tokens": 400, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(mp4).decode()}}]}]}
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={**AUTH, "Content-Type": "application/json"}, method="POST")
    t = time.time()
    try:
        d = json.load(urllib.request.urlopen(req, timeout=180))
        return path, time.time() - t, d["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return path, time.time() - t, f"ERR {e.code} {e.read()[:200]}"


with ThreadPoolExecutor(4) as ex:
    results = list(ex.map(one, sys.argv[2:]))
out = {}
for path, dt, text in results:
    out[path] = text
    print(f"=== {path} ({dt:.1f}s)\n{text.strip()}\n")
json.dump(out, open(sys.argv[1] + ".results.json", "w"), indent=1)
