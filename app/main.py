"""Vision Zero Street-Safety Agent — thin app on top of the team VSS archive.

Scans every indexed caption, parses the structured RISK/EVENT fields written by the
Vision Zero ingestion prompt, and serves a cross-camera incident feed. Clips captioned
with the generic prompt are scored by sending the video itself to Cosmos Reason with the
same prompt. W&B serverless inference writes incident reports and compiles watch rules.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import weave
except ImportError:
    weave = None


def traced(fn):
    return weave.op(fn) if weave else fn

PORT = int(os.environ.get("PORT", "8080"))
VSS_URL = os.environ["VSS_URL"].rstrip("/")
VSS_USERNAME = os.environ["VSS_USERNAME"]
VSS_PASSWORD = os.environ["VSS_PASSWORD"]
WANDB_API_KEY = os.environ.get("WANDB_API_KEY", "")
WANDB_PROJECT_HDR = f"{os.environ.get('WANDB_TEAM', '')}/{os.environ.get('WANDB_PROJECT', '')}"
WANDB_URL = "https://api.inference.wandb.ai/v1/chat/completions"
LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
COSMOS_URL = os.environ.get("COSMOS3_REASON_URL", "").rstrip("/")
GPU_BEARER_TOKEN = os.environ.get("GPU_BEARER_TOKEN", "")
COSMOS_MODEL = None
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "prompt.txt")) as _f:
    VZ_PROMPT = _f.read()
with open(os.path.join(HERE, "prompt_warehouse.txt")) as _f:
    WAREHOUSE_PROMPT = _f.read()
with open(os.path.join(HERE, "prompt_bomb.txt")) as _f:
    BOMB_PROMPT = _f.read()
# Demo assets and public-domain war clips live on the team VM, never in VSS.
FILES_URL = os.environ.get("VZ_FILES_URL", "").rstrip("/")
WAR_CLIP_RE = re.compile(r"^war/[a-z_]+/seg_\d+\.mp4$")
WAREHOUSE_CAMERAS = ("smartspace", "sdg_warehouse")

STREET_EVENTS = ["near_miss", "crosswalk_conflict", "bike_lane_blocked", "hard_brake",
                 "red_light_run", "dooring", "jaywalking", "illegal_turn"]
WAREHOUSE_EVENTS = ["path_conflict", "human_in_robot_path", "contact", "blocked_aisle"]
EVENT_TYPES = STREET_EVENTS + WAREHOUSE_EVENTS + ["none"]


def domain_of(camera_id):
    return "warehouse" if any(w in (camera_id or "") for w in WAREHOUSE_CAMERAS) else "street"


# ----------------------------------------------------------------------------- VSS client
class VSS:
    def __init__(self):
        self._tok = None
        self._lock = threading.Lock()

    def _login(self):
        body = json.dumps({"username": VSS_USERNAME, "password": VSS_PASSWORD}).encode()
        req = urllib.request.Request(VSS_URL + "/api/v1/auth/login", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            self._tok = json.load(r)["access_token"]

    def token(self):
        with self._lock:
            if not self._tok:
                self._login()
            return self._tok

    def call(self, method, path, params=None, body=None, timeout=90, retry=True):
        url = VSS_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token(),
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                with self._lock:
                    self._tok = None
                return self.call(method, path, params, body, timeout, retry=False)
            raise


vss = VSS()


# ----------------------------------------------------------------------------- parsing
FIELD_RE = {
    "risk": re.compile(r"RISK:\s*\**\s*<?\s*(\d)", re.I),
    "event": re.compile(r"EVENT:\s*\**\s*<?\s*([a-z_]+)", re.I),
    "peds_in_road": re.compile(r"PEDS_IN_ROAD:\s*\**\s*<?\s*(\d+)", re.I),
    "cyclists": re.compile(r"CYCLISTS:\s*\**\s*<?\s*(\d+)", re.I),
    "min_gap_m": re.compile(r"MIN_GAP_M:\s*\**\s*<?\s*([\d.]+)", re.I),
    "vulnerable": re.compile(r"VULNERABLE:\s*\**\s*<?\s*([a-z]+)", re.I),
    "machine": re.compile(r"MACHINE:\s*\**\s*<?\s*([a-z]+)", re.I),
    "humans_near": re.compile(r"HUMANS_NEAR:\s*\**\s*<?\s*(\d+)", re.I),
    "why": re.compile(r"WHY:\s*\**\s*(.+?)\s*$", re.I | re.S),
}


def parse_fields(text):
    """Pull the structured Vision Zero fields out of a caption. Returns {} if absent."""
    if not text or "RISK:" not in text.upper():
        return {}
    out = {}
    for k, rx in FIELD_RE.items():
        m = rx.search(text)
        if not m:
            continue
        v = m.group(1).strip()
        if k in ("risk", "peds_in_road", "cyclists", "humans_near"):
            try:
                v = int(v)
            except ValueError:
                continue
        elif k == "min_gap_m":
            try:
                v = float(v)
            except ValueError:
                continue
        elif k in ("machine", "vulnerable"):
            v = v.lower()
        elif k == "event":
            v = v.lower()
            if v not in EVENT_TYPES:
                v = "none" if v.startswith("non") else v
        elif k == "why":
            v = v.split("\n")[0].strip()
            if v.startswith("<") or v.lower() in ("none", "-", "n/a"):
                continue
        out[k] = v
    if "risk" in out:
        out["risk"] = max(0, min(5, out["risk"]))
    return reconcile(out)


def reconcile(f):
    """Cosmos sometimes rates a warehouse clip risky while reporting no human near a moving
    machine (e.g. an AGV passing a humanoid robot). Trust its own count over its score."""
    if f.get("humans_near") == 0 and f.get("risk"):
        f = {**f, "risk": 0, "event": "none"}
    return f


def narrative(text):
    """The free-text part of the caption, before the structured block."""
    if not text:
        return ""
    i = text.upper().find("RISK:")
    return (text[:i] if i > 0 else text).strip()


# ----------------------------------------------------------------------------- archive cache
LLM_SCORES_PATH = os.environ.get("LLM_SCORES_PATH", "/tmp/vz_video_scores.json")
REVIEWS_PATH = os.environ.get("REVIEWS_PATH", "/tmp/vz_reviews.json")


class Archive:
    def __init__(self):
        self.lock = threading.Lock()
        self.clips = []          # every segment row (dict)
        self.loaded_at = None
        self.loading = False
        self.error = None
        # source -> fields scored by the W&B LLM (fallback for generic-prompt clips).
        # Kept separately so a rescan of the archive does not wipe them.
        self.llm_scores = {}
        for path in (LLM_SCORES_PATH, os.path.join(HERE, "video_scores.json")):
            try:
                with open(path) as f:
                    self.llm_scores = json.load(f)
                break
            except (OSError, ValueError):
                continue
        # source -> {"verdict": "confirmed"|"dismissed", "note": str} from a human reviewer.
        self.reviews = {}
        for path in (REVIEWS_PATH, os.path.join(HERE, "reviews.json")):
            try:
                with open(path) as f:
                    self.reviews = json.load(f)
                break
            except (OSError, ValueError):
                continue

    def apply_review(self, c):
        r = self.reviews.get(c["source"])
        c["confirmed"] = bool(r and r["verdict"] == "confirmed")
        c["dismissed"] = bool(r and r["verdict"] == "dismissed")
        if c["dismissed"]:
            c["scored"] = False
        if r and r.get("note"):
            c["review_note"] = r["note"]

    def review(self, source, verdict, note):
        with self.lock:
            if verdict == "clear":
                self.reviews.pop(source, None)
            else:
                self.reviews[source] = {"verdict": verdict, "note": note or ""}
            for c in self.clips:
                if c["source"] == source:
                    c["scored"] = bool(c.get("risk") is not None)
                    c.pop("review_note", None)
                    self.apply_review(c)
            try:
                with open(REVIEWS_PATH, "w") as f:
                    json.dump(self.reviews, f)
            except OSError:
                pass

    def remember_llm_score(self, source, fields):
        with self.lock:
            self.llm_scores[source] = fields
            for c in self.clips:
                if c["source"] == source:
                    c.update({"scored": True, "llm_scored": True, **fields})
                    self.apply_review(c)
            try:
                with open(LLM_SCORES_PATH, "w") as f:
                    json.dump(self.llm_scores, f)
            except OSError:
                pass

    def refresh(self):
        with self.lock:
            if self.loading:
                return
            self.loading = True
        try:
            clips, offset, total = [], 0, None
            while total is None or offset < total:
                page = vss.call("GET", "/api/v1/videos/explore",
                                {"scope": "all", "limit": 100, "offset": offset})
                total = page.get("total", 0)
                for ch in page.get("chunks", []):
                    for seg in ch.get("timeline") or []:
                        txt = seg.get("reasoning_content") or ""
                        f = parse_fields(txt)
                        llm_scored = False
                        if not f and seg.get("source") in self.llm_scores:
                            f, llm_scored = reconcile(self.llm_scores[seg["source"]]), True
                        clips.append({
                            "source": seg.get("source"),
                            "original_video": ch.get("original_video"),
                            "filename": seg.get("filename") or ch.get("filename"),
                            "camera_id": ch.get("camera_id"),
                            "domain": domain_of(ch.get("camera_id")),
                            "location": ch.get("location"),
                            "capture_type": ch.get("capture_type"),
                            "chunk_index": ch.get("chunk_index"),
                            "segment_number": seg.get("segment_number"),
                            "start_sec": seg.get("segment_start_sec"),
                            "end_sec": seg.get("segment_end_sec"),
                            "upload_timestamp": ch.get("upload_timestamp"),
                            "caption": txt,
                            "narrative": narrative(txt),
                            "scored": bool(f),
                            "llm_scored": llm_scored,
                            **{k: f.get(k) for k in ("risk", "event", "peds_in_road", "cyclists",
                                                     "min_gap_m", "vulnerable", "machine",
                                                     "humans_near", "why")},
                        })
                        self.apply_review(clips[-1])
                offset += 100
                if not page.get("chunks"):
                    break
            with self.lock:
                self.clips = clips
                self.loaded_at = time.time()
                self.error = None
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.error = str(e)
        finally:
            with self.lock:
                self.loading = False

    def snapshot(self):
        with self.lock:
            return list(self.clips), self.loaded_at, self.loading, self.error


archive = Archive()


def stats(clips):
    cams = {}
    for c in clips:
        cam = cams.setdefault(c["camera_id"] or "unknown", {
            "camera_id": c["camera_id"], "domain": c["domain"], "location": c["location"],
            "clips": 0, "scored": 0,
            "risk_sum": 0, "max_risk": 0, "high_risk": 0, "events": {}})
        cam["clips"] += 1
        if c["scored"]:
            cam["scored"] += 1
            r = c["risk"] or 0
            cam["risk_sum"] += r
            cam["max_risk"] = max(cam["max_risk"], r)
            if r >= 3:
                cam["high_risk"] += 1
            ev = c["event"] or "none"
            if ev != "none":
                cam["events"][ev] = cam["events"].get(ev, 0) + 1
    for cam in cams.values():
        cam["avg_risk"] = round(cam["risk_sum"] / cam["scored"], 2) if cam["scored"] else None
        del cam["risk_sum"]
    scored = [c for c in clips if c["scored"]]
    return {
        "total_clips": len(clips),
        "scored_clips": len(scored),
        "high_risk_events": sum(1 for c in scored if (c["risk"] or 0) >= 3),
        "confirmed": sum(1 for c in clips if c.get("confirmed")),
        "dismissed": sum(1 for c in clips if c.get("dismissed")),
        "cameras": sorted(cams.values(), key=lambda x: (-(x["avg_risk"] or -1), x["camera_id"] or "")),
        "event_totals": {e: sum(1 for c in scored if c["event"] == e) for e in EVENT_TYPES if e != "none"},
    }


# ----------------------------------------------------------------------------- LLM (W&B inference)
@traced
def llm(messages, max_tokens=2500, temperature=0.2):
    if not WANDB_API_KEY:
        raise RuntimeError("WANDB_API_KEY not configured")
    # gpt-oss is a reasoning model: reasoning tokens count against max_tokens, so keep
    # the budget generous and the effort low or `content` comes back empty.
    body = json.dumps({"model": LLM_MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": temperature, "reasoning_effort": "low"}).encode()
    req = urllib.request.Request(WANDB_URL, data=body, method="POST", headers={
        "Authorization": "Bearer " + WANDB_API_KEY,
        "OpenAI-Project": WANDB_PROJECT_HDR,
        "Content-Type": "application/json",
        # Cloudflare in front of W&B inference rejects the default Python-urllib UA (1010).
        "User-Agent": "vision-zero-agent/0.1 (+vss-hackathon)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    choice = d["choices"][0]
    content = choice["message"].get("content")
    if not content:
        raise RuntimeError(f"LLM returned no content (finish_reason={choice.get('finish_reason')}); "
                           "raise max_tokens or lower reasoning effort")
    return content, d.get("usage", {})


def clip_block(c, i):
    return (f"[{i}] camera={c['camera_id']} location={c['location']} file={c['filename']} "
            f"t={c['start_sec']}-{c['end_sec']}s risk={c.get('risk')} event={c.get('event')} "
            f"gap_m={c.get('min_gap_m')} vulnerable={c.get('vulnerable')} "
            f"machine={c.get('machine')} humans_near={c.get('humans_near')}\n"
            f"    reviewer: {'confirmed' if c.get('confirmed') else 'not reviewed'}"
            f"{' - ' + c['review_note'] if c.get('review_note') else ''}\n"
            f"    why: {c.get('why')}\n    scene: {c['narrative'][:500]}")


@traced
def incident_report(clips, focus=None):
    lines = "\n".join(clip_block(c, i + 1) for i, c in enumerate(clips[:25]))
    warehouse = any(c.get("domain") == "warehouse" for c in clips[:25])
    street = any(c.get("domain") != "warehouse" for c in clips[:25])
    role = ("a safety analyst covering city streets (Vision Zero) and warehouse floors where "
            "AGVs, humanoid robots and forklifts share space with workers" if warehouse and street
            else "a warehouse safety analyst auditing robot/AGV/forklift interactions with workers"
            if warehouse else "a Vision Zero traffic-safety analyst")
    actions = ("engineering, enforcement, education for streets; layout, robot speed/geofence "
               "rules, PPE and training for warehouses" if warehouse and street
               else "aisle layout, robot speed limits and geofencing, PPE, training" if warehouse
               else "engineering, enforcement, education")
    sys_p = (f"You are {role}. You are given machine-generated observations of short video clips "
             "from multiple cameras. Write a concise incident report in markdown: "
             "1) Executive summary (2-3 sentences). 2) Top incidents, worst first, each citing "
             "the clip number in [n] form, with what happened and who was at risk. 3) Patterns "
             f"across cameras/locations. 4) Recommended actions ({actions}), each tied to "
             "evidence. Never invent details not in the observations. "
             "Machine distance estimates are rough and tend to overstate gaps. "
             "Where a human reviewer note is present it overrides the model's 'why'; lead with "
             "reviewer-confirmed clips and label unreviewed ones as unverified.")
    user = f"Observations:\n{lines}\n\n"
    user += f"Analyst focus: {focus}" if focus else "Produce the report."
    text, usage = llm([{"role": "system", "content": sys_p}, {"role": "user", "content": user}])
    return {"report": text, "model": LLM_MODEL, "usage": usage, "clips_used": len(clips[:25])}


def fetch_clip(source):
    """Download a segment MP4. Prefer a presigned S3 URL so bytes don't go through VSS."""
    try:
        res = vss.call("GET", "/api/v1/videos/playback-url",
                       {"source": source, "token": vss.token(), "expires_in": 600}, timeout=30)
        u = res.get("url") or res.get("playback_url") or res.get("presigned_url")
        if u:
            with urllib.request.urlopen(u, timeout=60) as r:
                return r.read()
    except Exception:  # noqa: BLE001
        pass
    url = VSS_URL + "/api/v1/videos/stream?" + urllib.parse.urlencode({"source": source, "token": vss.token()})
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def cosmos_model():
    global COSMOS_MODEL
    if not COSMOS_MODEL:
        req = urllib.request.Request(COSMOS_URL + "/v1/models", headers={"Authorization": "Bearer " + GPU_BEARER_TOKEN})
        with urllib.request.urlopen(req, timeout=20) as r:
            COSMOS_MODEL = json.load(r)["data"][0]["id"]
    return COSMOS_MODEL


@traced
def score_clip(c):
    """Score a clip by showing the video itself to Cosmos Reason with the Vision Zero prompt."""
    if not GPU_BEARER_TOKEN:
        raise RuntimeError("GPU_BEARER_TOKEN not configured")
    mp4 = fetch_clip(c["source"])
    body = {"model": cosmos_model(), "max_tokens": 700, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": WAREHOUSE_PROMPT if domain_of(c.get("camera_id")) == "warehouse" else VZ_PROMPT},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(mp4).decode()}}]}]}
    req = urllib.request.Request(COSMOS_URL + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + GPU_BEARER_TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    text = d["choices"][0]["message"].get("content") or ""
    f = parse_fields(text)
    if f:
        f["scored_by"] = "cosmos_video"
    return {"fields": f, "raw": text, "model": cosmos_model(), "usage": d.get("usage", {})}


@traced
def analyze_war_clip(video):
    """Show a public-domain war clip to Cosmos Reason with the bomb prompt (v2)."""
    with urllib.request.urlopen(FILES_URL + "/" + video, timeout=60) as r:
        mp4 = r.read()
    return {"video": video, **cosmos_bomb(mp4)}


def cosmos_bomb(mp4):
    if not GPU_BEARER_TOKEN:
        raise RuntimeError("GPU_BEARER_TOKEN not configured")
    body = {"model": cosmos_model(), "max_tokens": 400, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": BOMB_PROMPT},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(mp4).decode()}}]}]}
    req = urllib.request.Request(COSMOS_URL + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + GPU_BEARER_TOKEN, "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    text = d["choices"][0]["message"].get("content") or ""
    fields = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip().isupper():
            fields[k.strip().lower()] = v.strip()
    yes = lambda k: fields.get(k, "").lower().startswith("yes")  # noqa: E731
    return {"raw": text, "fields": fields, "model": cosmos_model(),
            "bomb_release": yes("bomb_release") or yes("bomb_falling"), "impact": yes("impact"),
            "latency_s": round(time.time() - t0, 1), "usage": d.get("usage", {})}


@traced
def compile_rule(rule, cameras):
    """Turn a natural-language watch rule into a filter the feed can apply."""
    cam_list = ", ".join(f"{c['camera_id']} ({c['location']})" for c in cameras if c["camera_id"])
    sys_p = ("Convert a street- or warehouse-safety watch rule into JSON with keys: min_risk (0-5; "
             "use 0 unless the rule names a severity), events (list from " + ",".join(EVENT_TYPES) +
             "; only when the rule names a specific kind of event such as a red light or dooring, "
             "leave empty for general 'comes near' or 'close call' rules), vulnerable (street road users only: list from pedestrian,"
             "cyclist,wheelchair,child or empty; leave empty for warehouse workers), machines "
             "(list from agv,humanoid,forklift or empty), cameras (list of "
             "camera_id values from the known cameras that match any place/camera mentioned, e.g. "
             "'NYC' -> all new_york cameras, 'warehouse' or 'robots' -> smartspace and "
             "sdg_warehouse cameras; empty if none mentioned), "
             "max_gap_m (number if the rule mentions a distance like 'within 1 meter', else null), "
             "query (short natural-language search phrase). Known cameras: " + cam_list +
             ". Return only JSON.")
    text, usage = llm([{"role": "system", "content": sys_p}, {"role": "user", "content": rule}],
                      max_tokens=1200, temperature=0)
    m = re.search(r"\{.*\}", text, re.S)
    spec = json.loads(m.group(0)) if m else {}
    return spec, usage


PROXIMITY_EVENTS = {"near_miss", "path_conflict", "human_in_robot_path", "contact"}
# Cosmos distance estimates are coarse and overstate gaps (frame checks: ~2 m reported for arm's reach).
GAP_TOLERANCE_M = 0.5


def apply_rule(clips, spec):
    out = []
    events = set(spec.get("events") or [])
    if events & PROXIMITY_EVENTS:
        events |= PROXIMITY_EVENTS
    spec = {**spec, "events": sorted(events)}
    for c in clips:
        if not c["scored"]:
            continue
        if (c["risk"] or 0) < max(1, int(spec.get("min_risk") or 0)):
            continue
        if spec.get("events") and c["event"] not in spec["events"]:
            continue
        if (spec.get("vulnerable") and c["domain"] != "warehouse"
                and (c["vulnerable"] or "none") not in spec["vulnerable"]):
            continue
        if spec.get("machines") and (c.get("machine") or "none") not in spec["machines"]:
            continue
        if spec.get("cameras") and not any(s in (c["camera_id"] or "") for s in spec["cameras"]):
            continue
        g = spec.get("max_gap_m")
        if g is not None and c["min_gap_m"] is not None and c["min_gap_m"] > float(g) + GAP_TOLERANCE_M:
            continue
        out.append(c)
    out.sort(key=lambda c: (-(c["risk"] or 0), not c.get("confirmed")))
    return out


# ----------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "VisionZero/0.1"

    def log_message(self, fmt, *args):  # quieter logs
        if "/api/stream" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # helpers
    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _file(self, name, ctype):
        with open(os.path.join(HERE, name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _stream(self, source):
        """Proxy a segment MP4 from VSS, honouring Range so <video> can seek."""
        self._proxy(VSS_URL + "/api/v1/videos/stream?" + urllib.parse.urlencode(
            {"source": source, "token": vss.token()}))

    def _proxy(self, url):
        hdrs = {}
        if self.headers.get("Range"):
            hdrs["Range"] = self.headers["Range"]
        req = urllib.request.Request(url, headers=hdrs)
        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            return
        self.send_response(resp.status)
        for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control"):
            if resp.headers.get(h):
                self.send_header(h, resp.headers[h])
        self.end_headers()
        try:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # routes
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path.rstrip("/") or "/"
        try:
            if p == "/":
                return self._file("index.html", "text/html; charset=utf-8")
            if p == "/health":
                return self._json({"ok": True})
            if p == "/war":
                return self._file("war.html", "text/html; charset=utf-8")
            if u.path in ("/files", "/files/", "/files/demo"):
                return self._redirect({"/files": "files/demo/", "/files/": "demo/", "/files/demo": "demo/"}[u.path])
            if p == "/api/war/live":
                return self._json({k: v for k, v in live.items() if k != "stop"})
            if p == "/api/war/live/clip":
                name = os.path.join(LIVE_DIR, f"seg_{int(q.get('i', -1)):03d}.mp4")
                if not os.path.isfile(name):
                    return self._json({"error": "not found"}, 404)
                with open(name, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if p.startswith("/files/"):
                rel = u.path[len("/files/"):]
                if ".." in rel.split("/"):
                    return self._json({"error": "not found"}, 404)
                return self._proxy(FILES_URL + "/" + urllib.parse.quote(urllib.parse.unquote(rel)))
            if p == "/api/status":
                clips, at, loading, err = archive.snapshot()
                return self._json({"clips": len(clips), "loaded_at": at, "loading": loading,
                                   "error": err, "model": LLM_MODEL, "prescore": prescore_state,
                                   "llm_scored": len(archive.llm_scores)})
            if p == "/api/stats":
                clips, at, loading, err = archive.snapshot()
                s = stats(clips)
                s.update({"loaded_at": at, "loading": loading})
                return self._json(s)
            if p == "/api/events":
                clips, _, _, _ = archive.snapshot()
                cam, ev, dom = q.get("camera"), q.get("event"), q.get("domain")
                min_risk = int(q.get("min_risk") or 0)
                limit = int(q.get("limit") or 60)
                include_unscored = q.get("unscored") == "1"
                rows = [c for c in clips
                        if (c["scored"] or include_unscored)
                        and (not cam or c["camera_id"] == cam)
                        and (not dom or c["domain"] == dom)
                        and (not ev or c["event"] == ev)
                        and (c["risk"] or 0) >= min_risk]
                if q.get("reviewed") == "1":
                    rows = [c for c in rows if c.get("confirmed")]
                rows.sort(key=lambda c: (-(c["risk"] if c["risk"] is not None else -1),
                                         not c.get("confirmed")))
                return self._json({"total": len(rows), "events": rows[:limit]})
            if p == "/api/stream":
                return self._stream(q["source"])
            if p == "/api/detections":
                try:
                    return self._json(vss.call("GET", "/api/v1/videos/detections", {"source": q["source"]}))
                except urllib.error.HTTPError as e:
                    return self._json({"detections": [], "note": f"no sidecar ({e.code})"})
            if p == "/api/search":
                body = {"query": q.get("q", ""), "top_k": int(q.get("top_k") or 20),
                        "llm_top_n": 1, "min_similarity": float(q.get("min_similarity") or 0.2)}
                if q.get("camera"):
                    body["metadata_filters"] = {"camera_id": q["camera"]}
                res = vss.call("POST", "/api/v1/search", body=body)
                hits = []
                for r in res.get("results", []):
                    txt = r.get("reasoning_content") or ""
                    f = parse_fields(txt)
                    hits.append({"source": r.get("source"), "camera_id": r.get("camera_id"),
                                 "location": r.get("location"), "similarity": r.get("similarity_score"),
                                 "filename": r.get("filename"), "narrative": narrative(txt),
                                 "caption": txt, "scored": bool(f), **f})
                return self._json({"hits": hits})
            self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            body = self._body()
            if p == "/api/refresh":
                threading.Thread(target=archive.refresh, daemon=True).start()
                return self._json({"started": True})
            if p == "/api/prescore":
                if prescore_state["running"]:
                    return self._json({"started": False, **prescore_state})
                threading.Thread(target=prescore, args=(
                    body.get("queries") or PRESCORE_QUERIES, int(body.get("per_query") or 12),
                    body.get("camera") or None, body.get("domain") or None), daemon=True).start()
                return self._json({"started": True})
            if p == "/api/war/live/start":
                film = body.get("film")
                if film not in LIVE_SOURCES:
                    return self._json({"error": "unknown film"}, 400)
                if live["running"]:
                    return self._json({"error": "a live feed is already running"}, 409)
                live.update(running=True, stop=False, film=film, segments=[], alerts=[], error=None,
                            started_at=time.time())
                threading.Thread(target=run_live, args=(film, max(1, min(int(body.get("max_segments") or 24), 60)),
                                                        bool(body.get("realtime", True))), daemon=True).start()
                return self._json({"started": True})
            if p == "/api/war/live/stop":
                live["stop"] = True
                return self._json({"stopping": True})
            if p == "/api/war/analyze":
                video = body.get("video", "")
                if not WAR_CLIP_RE.match(video):
                    return self._json({"error": "unknown clip"}, 400)
                return self._json(analyze_war_clip(video))
            if p == "/api/report":
                clips, _, _, _ = archive.snapshot()
                by_src = {c["source"]: c for c in clips}
                chosen = [by_src[s] for s in body.get("sources", []) if s in by_src]
                if not chosen:
                    cam = body.get("camera")
                    chosen = sorted([c for c in clips if c["scored"] and (not cam or c["camera_id"] == cam)],
                                    key=lambda c: (not c.get("confirmed"), -(c["risk"] or 0)))[:15]
                if not chosen:
                    return self._json({"error": "no scored clips yet"}, 400)
                return self._json(incident_report(chosen, body.get("focus")))
            if p == "/api/review":
                verdict = body.get("verdict")
                if verdict not in ("confirmed", "dismissed", "clear") or not body.get("source"):
                    return self._json({"error": "need source and verdict confirmed|dismissed|clear"}, 400)
                archive.review(body["source"], verdict, body.get("note"))
                return self._json({"ok": True})
            if p == "/api/score":
                clips, _, _, _ = archive.snapshot()
                c = next((x for x in clips if x["source"] == body.get("source")), None)
                if not c:
                    return self._json({"error": "unknown source"}, 404)
                res = score_clip(c)
                if res["fields"]:
                    archive.remember_llm_score(c["source"], res["fields"])
                return self._json(res)
            if p == "/api/ask":
                req = {"query": body.get("question", ""), "top_k": 10, "min_similarity": 0.2}
                if body.get("camera"):
                    req["metadata_filters"] = {"camera_id": body["camera"]}
                res = vss.call("POST", "/api/v1/agent/search-and-answer", body=req, timeout=150)
                ev = (res.get("evidence") or {})
                chunks = ev.get("chunks") or []
                clips, _, _, _ = archive.snapshot()
                cam_of = {c["original_video"]: c["camera_id"] for c in clips}
                return self._json({"answer": res.get("answer"),
                                   "chunks": [{"original_video": c.get("original_video"),
                                               "preview_source": c.get("preview_source"),
                                               "camera_id": c.get("camera_id") or cam_of.get(c.get("original_video")),
                                               "start": c.get("best_match_start_sec"),
                                               "score": c.get("similarity_score")} for c in chunks[:8]]})
            if p == "/api/rule":
                clips, _, _, _ = archive.snapshot()
                spec, usage = compile_rule(body.get("rule", ""), stats(clips)["cameras"])
                matches = apply_rule(clips, spec)
                return self._json({"spec": spec, "total": len(matches), "matches": matches[:40],
                                   "usage": usage})
            self._json({"error": "not found"}, 404)
        except urllib.error.HTTPError as e:
            self._json({"error": f"upstream {e.code}: {e.read()[:300].decode(errors='ignore')}"}, 502)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, 500)


PRESCORE_QUERIES = [
    "pedestrian steps into the road in front of a moving car",
    "cyclist squeezed between bus and parked cars",
    "vehicle blocking the bike lane",
    "car runs a red light while pedestrians cross",
    "jaywalking across busy traffic",
    "vehicle braking hard to avoid pedestrian",
    "cyclist riding close to a car door",
    "person close to a moving vehicle",
]
prescore_state = {"running": False, "done": 0, "total": 0, "errors": 0, "started_at": None}


def prescore(queries, per_query, camera=None, domain=None):
    """Find likely-risky clips by semantic search, then video-score the unscored ones.
    For the warehouse domain the archive is small enough to score every clip."""
    prescore_state.update({"running": True, "done": 0, "total": 0, "errors": 0, "started_at": time.time()})
    try:
        clips, _, _, _ = archive.snapshot()
        by_src = {c["source"]: c for c in clips}
        todo = {}
        if domain == "warehouse":
            todo = {c["source"]: c for c in clips
                    if c["domain"] == "warehouse" and not c["scored"] and not c.get("dismissed")}
            queries = []
        for q in queries:
            body = {"query": q, "top_k": per_query, "llm_top_n": 1, "min_similarity": 0.25}
            if camera:
                body["metadata_filters"] = {"camera_id": camera}
            try:
                for r in vss.call("POST", "/api/v1/search", body=body).get("results", []):
                    c = by_src.get(r.get("source"))
                    if c and not c["scored"]:
                        todo[c["source"]] = c
            except Exception:  # noqa: BLE001
                prescore_state["errors"] += 1
        prescore_state["total"] = len(todo)

        def one(c):
            try:
                res = score_clip(c)
                if res["fields"]:
                    archive.remember_llm_score(c["source"], res["fields"])
            except Exception:  # noqa: BLE001
                prescore_state["errors"] += 1
            prescore_state["done"] += 1

        threads = []
        items = list(todo.values())
        for i in range(0, len(items), 1):
            t = threading.Thread(target=one, args=(items[i],), daemon=True)
            threads.append(t)
            t.start()
            if len(threads) >= 4:
                for t in threads:
                    t.join()
                threads = []
        for t in threads:
            t.join()
    finally:
        prescore_state["running"] = False


LIVE_SOURCES = {"disney": "war/src/disney.ogv", "special_delivery": "war/src/special_delivery.webm",
                "tirpitz": "war/src/tirpitz.ogv"}
LIVE_DIR = "/tmp/vz_live"
SEGMENT_S = 5
live = {"running": False, "stop": False, "film": None, "segments": [], "alerts": [], "error": None,
        "started_at": None, "email": "smtp" if os.environ.get("SMTP_HOST") else None,
        "webhook": bool(os.environ.get("ALERT_WEBHOOK_URL"))}


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def send_alert(alert):
    """Email (if SMTP_* is configured) and/or webhook (if ALERT_WEBHOOK_URL is set); otherwise record the draft."""
    subject = f"[Vision Zero] Explosion detected: {alert['film']} at {alert['at']}"
    body = (f"Cosmos Reason flagged an explosion in the live feed.\n\nFeed: {alert['film']}\n"
            f"Segment: #{alert['segment']} ({alert['at']}, {SEGMENT_S} s)\nCosmos: {alert['why']}\n\n"
            f"Clip: {alert['clip_url']}\n")
    alert["email_subject"], alert["email_body"] = subject, body
    status = []
    if os.environ.get("SMTP_HOST") and os.environ.get("ALERT_EMAIL_TO"):
        import smtplib
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"], msg["To"] = subject, os.environ["ALERT_EMAIL_TO"]
        msg["From"] = os.environ.get("ALERT_EMAIL_FROM") or os.environ.get("SMTP_USER") or "vision-zero@localhost"
        msg.set_content(body)
        try:
            with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=20) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
            status.append("email sent")
        except Exception as e:  # noqa: BLE001
            status.append(f"email failed: {type(e).__name__}")
    if os.environ.get("ALERT_WEBHOOK_URL"):
        try:
            req = urllib.request.Request(os.environ["ALERT_WEBHOOK_URL"], method="POST",
                                         data=json.dumps({"text": subject + "\n" + body}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).close()
            status.append("webhook sent")
        except Exception as e:  # noqa: BLE001
            status.append(f"webhook failed: {type(e).__name__}")
    alert["delivery"] = ", ".join(status) or "email not sent: SMTP not configured (draft below)"


def run_live(film, max_segments, realtime):
    import shutil
    import subprocess
    try:
        shutil.rmtree(LIVE_DIR, ignore_errors=True)
        os.makedirs(LIVE_DIR)
        ext = os.path.splitext(LIVE_SOURCES[film])[1]
        src = os.path.join(LIVE_DIR, "src" + ext)
        with urllib.request.urlopen(FILES_URL + "/" + LIVE_SOURCES[film], timeout=120) as r, open(src, "wb") as f:
            shutil.copyfileobj(r, f)
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", src, "-t", str(max_segments * SEGMENT_S),
                        "-vf", "scale=-2:360", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                        "-f", "segment", "-segment_time", str(SEGMENT_S), "-reset_timestamps", "1",
                        "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_S})",
                        os.path.join(LIVE_DIR, "seg_%03d.mp4")], check=True, timeout=600)
        segs = sorted(p for p in os.listdir(LIVE_DIR) if p.startswith("seg_"))
        live["segments"] = [{"i": i, "at": f"{i * SEGMENT_S // 60}:{i * SEGMENT_S % 60:02d}", "state": "pending"}
                            for i in range(len(segs))]
        for i, name in enumerate(segs):
            if live["stop"]:
                break
            t0 = time.time()
            seg = live["segments"][i]
            seg["state"] = "checking"
            try:
                with open(os.path.join(LIVE_DIR, name), "rb") as f:
                    res = cosmos_bomb(f.read())
                seg.update(state="explosion" if res["impact"] else "clear", why=res["fields"].get("why", ""),
                           release=res["bomb_release"], latency_s=res["latency_s"])
                if res["impact"]:
                    alert = {"film": film, "segment": i, "at": seg["at"], "why": seg["why"], "ts": time.time(),
                             "clip": f"api/war/live/clip?i={i}",
                             "clip_url": (os.environ.get("VZ_PUBLIC_URL", "").rstrip("/") + "/" if os.environ.get("VZ_PUBLIC_URL") else "")
                             + f"api/war/live/clip?i={i}"}
                    send_alert(alert)
                    live["alerts"].insert(0, alert)
            except Exception as e:  # noqa: BLE001
                seg.update(state="error", why=str(e)[:200])
            if realtime:
                time.sleep(max(0, SEGMENT_S - (time.time() - t0)))
    except Exception as e:  # noqa: BLE001
        live["error"] = str(e)[:300]
    finally:
        live["running"] = False


def background_refresh():
    while True:
        archive.refresh()
        time.sleep(600)


if __name__ == "__main__":
    # Traces may go to a different W&B account than inference; WANDB_API_KEY above is already
    # captured for inference, so overriding the env var only affects Weave.
    if os.environ.get("WEAVE_WANDB_API_KEY"):
        os.environ["WANDB_API_KEY"] = os.environ["WEAVE_WANDB_API_KEY"]
    weave_project = os.environ.get("WEAVE_PROJECT") or WANDB_PROJECT_HDR
    if weave and os.environ.get("WANDB_API_KEY"):
        try:
            weave.init(weave_project)
        except Exception as e:  # noqa: BLE001
            print(f"weave tracing disabled: {e}", flush=True)
            weave = None
    threading.Thread(target=background_refresh, daemon=True).start()
    print(f"Vision Zero agent on 0.0.0.0:{PORT} -> {VSS_URL} (LLM {LLM_MODEL})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
