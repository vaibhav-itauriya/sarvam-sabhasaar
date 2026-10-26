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
    def transcribe_short(self, audio: bytes, filename: str = "question.webm",
                         language_code: str = "unknown") -> tuple[str, str | None]:
        """Up to ~30 s of audio: spoken questions and live caption chunks."""
        resp = self.client.speech_to_text.transcribe(
            file=(filename, audio),
            model=config.STT_MODEL,
            language_code=language_code or "unknown",
        )
        return resp.transcript, getattr(resp, "language_code", None)

    # ---------- Sarvam-105B ----------
    def chat(self, messages: list[dict], max_tokens: int = 4096, json_mode: bool = False,
             reasoning_effort: str = "low") -> str:
        """reasoning_effort: "low" | "medium" | "high" | "max", or "off" to switch reasoning off."""
        def build(effort: str, tokens: int, use_json: bool) -> dict:
            kw = dict(messages=messages, model=config.LLM_MODEL, temperature=0.2, max_tokens=tokens)
            # The API treats an explicit null as "no reasoning"; leaving it out means the default (medium)
            kw["reasoning_effort"] = None if effort == "off" else effort
            if use_json:
                kw["response_format"] = {"type": "json_object"}
            return kw

        # sarvam-105b reasons before answering and the reasoning counts against max_tokens, so a reply
        # can come back empty (finish_reason=length). Retry with more room, then with reasoning switched
        # off (or back on, if it was off and the API refused that), then without JSON mode.
        alt = "low" if reasoning_effort == "off" else "off"
        attempts = [build(reasoning_effort, max_tokens, json_mode),
                    build(reasoning_effort, max(max_tokens * 2, 6000), json_mode),
                    build(alt, max(max_tokens, 4000), json_mode)]
        if json_mode:
            attempts.append(build(alt, max(max_tokens, 4000), False))
        last = ""
        for i, kw in enumerate(attempts):
            try:
                resp = self.client.chat.completions(**kw)
            except Exception as e:
                if i == len(attempts) - 1:
                    raise
                print(f"[sabhasaar] 105B call failed ({type(e).__name__}: {str(e)[:200]}), trying next setting", flush=True)
                continue
            choice = resp.choices[0]
            content = (choice.message.content or "").strip()
            reasoning = (getattr(choice.message, "reasoning_content", None) or "").strip()
            if not content and json_mode and "{" in reasoning:
                # some replies put the final JSON at the end of the reasoning instead of the content
                content = last_json_object(reasoning) or ""
            last = getattr(choice, "finish_reason", "") or ""
            usage = getattr(resp, "usage", None)
            print(f"[sabhasaar] 105B reply: {len(content)} chars, reasoning {len(reasoning)} chars, "
                  f"effort={kw['reasoning_effort']}, finish={last}, tokens={getattr(usage, 'total_tokens', '?')}", flush=True)
            if content:
                return content
        raise SarvamError(f"Sarvam-105B returned an empty answer (finish_reason={last}). Try again.")

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


def last_json_object(text: str) -> str | None:
    """The last complete {...} object in text, or None."""
    end = text.rfind("}")
    for start in [i for i, ch in enumerate(text[:end]) if ch == "{"]:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return text[start : end + 1]
        except json.JSONDecodeError:
            continue
    return None


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
