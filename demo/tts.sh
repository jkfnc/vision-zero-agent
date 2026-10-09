#!/usr/bin/env bash
# Re-voice demo narration with Piper TTS running in a throwaway k8s pod.
#
# Usage: /home/jayakumaronline/vision-zero-work/demo/tts.sh [narration.json]      (default: /home/jayakumaronline/vision-zero-work/demo/narration.json)
#   narration.json = [{"id": "...", "text": "..."}, ...]
# Outputs (in $OUT_DIR, default /home/jayakumaronline/vision-zero-work/demo/audio):
#   <id>.wav        raw Piper output (22.05 kHz)
#   <id>.norm.wav   48 kHz mono, loudnorm -16 LUFS, 0.25 s lead-in, 0.4 s tail
#   durations.json  id -> seconds of <id>.norm.wav
#
# Env overrides: VOICE (en_US-lessac-medium | en_US-ryan-high | any rhasspy/piper-voices en_US name),
#   LENGTH_SCALE (1.05), SENTENCE_SILENCE (0.35), OUT_DIR, KEEP_POD=1 to leave the pod running.
set -euo pipefail

NARRATION="${1:-/home/jayakumaronline/vision-zero-work/demo/narration.json}"
OUT_DIR="${OUT_DIR:-/home/jayakumaronline/vision-zero-work/demo/audio}"
VOICE="${VOICE:-en_US-lessac-medium}"
LENGTH_SCALE="${LENGTH_SCALE:-1.05}"
SENTENCE_SILENCE="${SENTENCE_SILENCE:-0.35}"
KEEP_POD="${KEEP_POD:-0}"

export KUBECONFIG="${KUBECONFIG:-/config/team-4-k8s.yaml}"
NS=team-4
POD=tts-worker
K="kubectl -n $NS"

log() { echo "[tts] $*" >&2; }

[ -f "$NARRATION" ] || { echo "narration file not found: $NARRATION" >&2; exit 1; }
jq -e 'type=="array" and all(.[]; (.id|type=="string") and (.id|test("^[A-Za-z0-9_.-]+$")) and (.text|type=="string"))' \
  "$NARRATION" >/dev/null || { echo "narration must be a JSON list of {id, text} with filename-safe ids" >&2; exit 1; }
mkdir -p "$OUT_DIR"

# --- 1. ensure pod ----------------------------------------------------------
phase="$($K get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
if [ -n "$phase" ] && [ "$phase" != "Running" ] && [ "$phase" != "Pending" ]; then
  log "pod $POD is $phase; recreating"
  $K delete pod "$POD" --wait=true >/dev/null
  phase=""
fi
if [ -z "$phase" ]; then
  log "creating pod $POD"
  $K run "$POD" --image=python:3.12-slim --restart=Never --labels=app=tts-worker \
    --overrides='{"spec":{"containers":[{"name":"tts-worker","image":"python:3.12-slim","command":["sleep","7200"],"resources":{"requests":{"cpu":"2","memory":"2Gi"},"limits":{"cpu":"4","memory":"4Gi"}}}]}}' >/dev/null
fi
$K wait --for=condition=Ready "pod/$POD" --timeout=300s >/dev/null

# --- 2. ensure piper + voice ------------------------------------------------
log "ensuring piper-tts and voice $VOICE"
$K exec "$POD" -- env VOICE="$VOICE" sh -euc '
python -c "import piper" 2>/dev/null || pip install -q --root-user-action=ignore --disable-pip-version-check piper-tts >/dev/null
mkdir -p /voices
if [ ! -s "/voices/$VOICE.onnx" ] || [ ! -s "/voices/$VOICE.onnx.json" ]; then
  python - "$VOICE" <<"EOF"
import os, sys, urllib.request
v = sys.argv[1]                      # e.g. en_US-lessac-medium
lang, name, quality = v.split("-", 2)
base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang[:2]}/{lang}/{name}/{quality}/{v}"
for ext in (".onnx", ".onnx.json"):
    urllib.request.urlretrieve(base + ext, f"/voices/{v}{ext}.part")
    os.replace(f"/voices/{v}{ext}.part", f"/voices/{v}{ext}")
EOF
fi'

# --- 3. synthesize in pod ---------------------------------------------------
$K exec "$POD" -- sh -c 'rm -rf /work && mkdir -p /work/out'
$K cp "$NARRATION" "$NS/$POD:/work/narration.json" >/dev/null
log "synthesizing $(jq length "$NARRATION") items"
$K exec -i "$POD" -- env VOICE="$VOICE" LENGTH_SCALE="$LENGTH_SCALE" SENTENCE_SILENCE="$SENTENCE_SILENCE" python - <<'EOF'
import json, os, subprocess
items = json.load(open("/work/narration.json"))
for it in items:
    txt = f"/work/{it['id']}.txt"
    with open(txt, "w") as f:
        f.write(" ".join(it["text"].split()) + "\n")
    subprocess.run(["python", "-m", "piper", "-m", f"/voices/{os.environ['VOICE']}.onnx",
                    "-i", txt, "-f", f"/work/out/{it['id']}.wav",
                    "--length-scale", os.environ["LENGTH_SCALE"],
                    "--sentence-silence", os.environ["SENTENCE_SILENCE"]],
                   check=True, stderr=subprocess.DEVNULL)
    print("  synthesized", it["id"], flush=True)
EOF

# --- 4. copy back, normalize, measure ---------------------------------------
durations='{}'
for id in $(jq -r '.[].id' "$NARRATION"); do
  raw="$OUT_DIR/$id.wav"; norm="$OUT_DIR/$id.norm.wav"
  $K cp "$NS/$POD:/work/out/$id.wav" "$raw" >/dev/null
  m="$(ffmpeg -hide_banner -nostats -i "$raw" -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 \
       | sed -n '/^{/,/^}/p')"
  ln="loudnorm=I=-16:TP=-1.5:LRA=11:linear=true"
  ln+=":measured_I=$(jq -r .input_i <<<"$m"):measured_TP=$(jq -r .input_tp <<<"$m")"
  ln+=":measured_LRA=$(jq -r .input_lra <<<"$m"):measured_thresh=$(jq -r .input_thresh <<<"$m")"
  ln+=":offset=$(jq -r .target_offset <<<"$m")"
  ffmpeg -hide_banner -loglevel error -y -i "$raw" \
    -af "$ln,aresample=48000,adelay=250:all=1,apad=pad_dur=0.4" \
    -ar 48000 -ac 1 -c:a pcm_s16le "$norm"
  d="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$norm")"
  durations="$(jq --arg id "$id" --argjson d "$d" '. + {($id): ($d*1000|round/1000)}' <<<"$durations")"
  log "$id  ${d}s"
done
jq . <<<"$durations" > "$OUT_DIR/durations.json"
log "total $(jq '[.[]]|add|.*100|round/100' "$OUT_DIR/durations.json")s -> $OUT_DIR/durations.json"

# --- 5. cleanup -------------------------------------------------------------
if [ "$KEEP_POD" != "1" ]; then
  $K delete pod "$POD" --wait=false >/dev/null && log "deleted pod $POD"
fi
