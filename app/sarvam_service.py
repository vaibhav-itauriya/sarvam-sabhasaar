"""Thin wrapper around the Sarvam AI SDK.

Everything that talks to Sarvam lives here, so the rest of the app can be tested
with a fake that has the same four methods.
"""
from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import wave
from pathlib import Path

from sarvamai import SarvamAI

from . import config


class SarvamError(RuntimeError):
    pass


class SarvamService:
    def __init__(self, api_key: str):
        if not api_key:
            raise SarvamError("SARVAM_API_KEY is not set. Add it to the .env file.")
        self.client = SarvamAI(api_subscription_key=api_key)

    # ---------- Saaras: long meeting audio, with speakers ----------
    def transcribe_meeting(
        self,
        audio_path: Path,
        language_code: str = "unknown",
        num_speakers: int | None = None,
        keyterms: list[str] | None = None,
        mode: str = "transcribe",
    ) -> dict:
        """Batch STT with diarization. Returns the raw Sarvam output JSON."""
        kwargs = dict(
            model=config.STT_MODEL,
            mode=mode,
            language_code=language_code or "unknown",
            with_diarization=True,
            with_timestamps=True,
        )
        if num_speakers:
            kwargs["num_speakers"] = int(num_speakers)
        if keyterms and config.STT_MODEL.endswith("v4"):
            kwargs["keyterms"] = [k[:64] for k in keyterms][:50]

        job = self.client.speech_to_text_job.create_job(**kwargs)
        job.upload_files(file_paths=[str(audio_path)], timeout=600)
        job.start()
        job.wait_until_complete(poll_interval=5, timeout=60 * 60)
        results = job.get_file_results()
        if results.get("failed"):
            msg = results["failed"][0].get("error_message") or "transcription failed"
            raise SarvamError(f"Saaras batch job failed: {msg}")
        with tempfile.TemporaryDirectory() as tmp:
            job.download_outputs(output_dir=tmp)
            outputs = list(Path(tmp).glob("*.json"))
            if not outputs:
                raise SarvamError("Saaras batch job returned no output file")
            return json.loads(outputs[0].read_text(encoding="utf-8"))

    # ---------- Saaras: short spoken question ----------
    def transcribe_short(self, audio: bytes, filename: str = "question.webm") -> tuple[str, str | None]:
        resp = self.client.speech_to_text.transcribe(
            file=(filename, audio),
            model=config.STT_MODEL,
            language_code="unknown",
        )
        return resp.transcript, getattr(resp, "language_code", None)

    # ---------- Sarvam-105B ----------
    def chat(self, messages: list[dict], max_tokens: int = 4096, json_mode: bool = False,
             reasoning_effort: str | None = "low") -> str:
        kwargs = dict(
            messages=messages,
            model=config.LLM_MODEL,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions(**kwargs)
        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise SarvamError("Sarvam-105B returned an empty answer (try a larger max_tokens)")
        return content

    # ---------- Bulbul ----------
    def speak(self, text: str, language_code: str) -> bytes:
        """Text to a single WAV. Splits long text to stay under Bulbul's 2500-char limit."""
        chunks = split_for_tts(text, limit=1800)
        wavs = []
        for chunk in chunks:
            resp = self.client.text_to_speech.convert(
                text=chunk,
                language_code=language_code,
                speaker=config.TTS_SPEAKER,
                model=config.TTS_MODEL,
                speech_sample_rate=22050,
            )
            wavs.extend(base64.b64decode(a) for a in resp.audios)
        return join_wavs(wavs)


# ---------- helpers (pure, tested) ----------
def parse_json(text: str) -> dict:
    """Pull a JSON object out of a model reply, tolerating ``` fences and chatter."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise SarvamError("Model did not return JSON")
        return json.loads(text[start : end + 1])


def split_for_tts(text: str, limit: int = 1800) -> list[str]:
    sentences = re.split(r"(?<=[.!?।॥])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        while len(s) > limit:  # a single giant sentence
            chunks.append(s[:limit])
            s = s[limit:]
        if len(cur) + len(s) + 1 > limit and cur:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [""]


def join_wavs(wavs: list[bytes]) -> bytes:
    if len(wavs) == 1:
        return wavs[0]
    out = io.BytesIO()
    params = None
    frames = []
    for w in wavs:
        with wave.open(io.BytesIO(w)) as r:
            if params is None:
                params = r.getparams()
            frames.append(r.readframes(r.getnframes()))
    with wave.open(out, "wb") as wr:
        wr.setparams(params)
        for f in frames:
            wr.writeframes(f)
    return out.getvalue()
