"""Voice the sample Gram Sabha with a different Bulbul voice per speaker.

Gives you:
  app/demo/gram_sabha.mp3 (or .wav without ffmpeg)   a ~9 minute, 5-speaker Hindi meeting recording
  timings in app/demo/gram_sabha.json updated to match the audio

Then either
  - restart the app with  python -m scripts.reset_demo  so the sample meeting plays audio, or
  - upload app/demo/gram_sabha.mp3 as a new meeting to test the real Saaras -> 105B pipeline.

Run from the project root:  python -m scripts.make_demo_audio
"""
import base64
import shutil
import io
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sarvamai import SarvamAI  # noqa: E402

from app import config  # noqa: E402

try:  # Windows terminals often can't print Hindi otherwise
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VOICES = {"0": "rahul", "1": "ritu", "2": "kavya", "3": "amit", "4": "aditya"}  # bulbul:v3 voices
PAUSE_S = 0.8

if not config.SARVAM_API_KEY:
    sys.exit("Set SARVAM_API_KEY in .env first")

client = SarvamAI(api_subscription_key=config.SARVAM_API_KEY)
demo_path = ROOT / "app" / "demo" / "gram_sabha.json"
data = json.loads(demo_path.read_text(encoding="utf-8"))
segments = data["transcript"]["segments"]

frames, params, t = [], None, 0.0
for seg in segments:
    print(f"Voicing turn {seg['id'] + 1} of {len(segments)} (speaker {seg['speaker']})", flush=True)
    resp = client.text_to_speech.convert(text=seg["text"], language_code="hi-IN",
                                         speaker=VOICES.get(seg["speaker"], "shubh"),
                                         model="bulbul:v3", speech_sample_rate=22050)
    wav = base64.b64decode(resp.audios[0])
    with wave.open(io.BytesIO(wav)) as r:
        params = params or r.getparams()
        f = r.readframes(r.getnframes())
        dur = r.getnframes() / r.getframerate()
    silence = b"\x00" * int(params.framerate * PAUSE_S) * params.sampwidth * params.nchannels
    seg["start"], seg["end"] = round(t, 2), round(t + dur, 2)
    frames += [f, silence]
    t += dur + PAUSE_S

with tempfile.TemporaryDirectory() as tmp:
    wav_path = Path(tmp) / "meeting.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setparams(params)
        for f in frames:
            w.writeframes(f)
    demo_dir = ROOT / "app" / "demo"
    for old in (demo_dir / "gram_sabha.mp3", demo_dir / "gram_sabha.wav"):
        old.unlink(missing_ok=True)
    if shutil.which("ffmpeg"):
        out = demo_dir / "gram_sabha.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path), "-ac", "1", "-ar", "16000",
                        "-b:a", "48k", str(out)], check=True)
    else:  # no ffmpeg: keep the WAV (bigger file, works the same)
        out = demo_dir / "gram_sabha.wav"
        shutil.copy(wav_path, out)

data["meeting"]["duration"] = round(t, 1)
for qa in data["qa"]:
    for r in qa["refs"]:
        s = segments[r["seg"]]["start"]
        r["start"] = s
        r["label"] = f"{int(s)//3600:02d}:{int(s)%3600//60:02d}:{int(s)%60:02d}"
demo_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nWrote {out} ({t/60:.1f} min). Now run: python -m scripts.reset_demo")
