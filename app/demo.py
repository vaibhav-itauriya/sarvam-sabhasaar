"""Seeds a sample Gram Sabha so the app has something to show before any upload."""
import json
import shutil
from pathlib import Path

from . import store

DEMO_ID = "demogramsabha"
DEMO_DIR = Path(__file__).parent / "demo"


def seed_demo(force: bool = False) -> None:
    target = store.mdir(DEMO_ID)
    if target.exists() and not force:
        return
    data = json.loads((DEMO_DIR / "gram_sabha.json").read_text(encoding="utf-8"))
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    meeting = data["meeting"]
    audio = next((p for p in (DEMO_DIR / "gram_sabha.mp3", DEMO_DIR / "gram_sabha.wav") if p.exists()), None)
    if audio:  # created by scripts/make_demo_audio.py
        dest = target / f"audio{audio.suffix}"
        shutil.copy(audio, dest)
        from .pipeline import probe_duration, sha256
        meeting.update(audio_file=dest.name, audio_sha256=sha256(dest),
                       duration=probe_duration(dest) or meeting.get("duration"),
                       source_name=f"{audio.name} (voiced with Bulbul)")
    store.write(DEMO_ID, "meeting", meeting)
    store.write(DEMO_ID, "transcript", data["transcript"])
    store.write(DEMO_ID, "minutes", data["minutes"])
    store.write(DEMO_ID, "qa", data["qa"])
