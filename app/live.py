"""Live recording.

Three ways in, one pipeline:
  - "tab":  the browser records the sound of another tab (Google Meet, Zoom, YouTube live of Sansad TV,
            a court live stream) and optionally the microphone, and posts ~25 s WAV chunks
  - "mic":  the browser records the room through the microphone, same chunks
  - "link": the server records a stream URL itself with ffmpeg (yt-dlp finds the audio of YouTube links)

Each chunk is captioned with Saaras right away, so people can follow along and ask questions while the
meeting is still going. On Stop the chunks are joined into one recording and the normal pipeline runs:
Saaras batch with speaker separation, then Sarvam-105B minutes.
"""
from __future__ import annotations

import io
import ipaddress
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from . import pipeline, store

RATE = 16000
CHUNK_SECONDS = 25            # Saaras REST takes up to ~30 s per request
MAX_SECONDS = 4 * 3600        # stop a forgotten recording after 4 hours
SILENCE_RMS = 60              # below this a chunk is treated as silence and not sent to Saaras
DIRECT_MEDIA = (".m3u8", ".mp3", ".aac", ".m4a", ".wav", ".ogg", ".opus", ".mp4", ".webm", ".ts", ".flac")

_pool = ThreadPoolExecutor(max_workers=4)
_cap_lock = threading.Lock()
_links: dict[str, dict] = {}   # mid -> {"stop": Event, "proc": Popen | None}


class LiveError(ValueError):
    pass


def live_dir(mid: str) -> Path:
    d = store.mdir(mid) / "live"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- chunks and captions ----------------
def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def _rms(pcm: bytes) -> float:
    import array
    a = array.array("h", pcm[: len(pcm) // 2 * 2])
    if not a:
        return 0.0
    step = max(1, len(a) // 4000)
    sample = a[::step]
    return (sum(x * x for x in sample) / len(sample)) ** 0.5


def add_chunk(svc, mid: str, seq: int, wav_bytes: bytes, caption: bool = True) -> dict:
    meta = store.read(mid, "meeting")
    if not meta or meta.get("status") != "live":
        raise LiveError("This meeting is not recording any more")
    try:
        with wave.open(io.BytesIO(wav_bytes)) as r:
            if r.getnchannels() != 1 or r.getsampwidth() != 2:
                raise LiveError("Chunks must be 16-bit mono WAV")
            rate, pcm = r.getframerate(), r.readframes(r.getnframes())
    except wave.Error as e:
        raise LiveError(f"Not a WAV chunk: {e}")
    if rate != RATE:
        raise LiveError(f"Chunks must be {RATE} Hz")
    duration = len(pcm) / 2 / rate
    (live_dir(mid) / f"chunk-{seq:06d}.wav").write_bytes(wav_bytes)
    silent = _rms(pcm) < SILENCE_RMS

    with _cap_lock:
        caps = store.read(mid, "captions", [])
        for c in caps:
            if c["seq"] == seq:   # a retried upload
                return c
        caps.append({"seq": seq, "start": 0.0, "end": 0.0, "duration": duration, "text": None,
                     "status": "silent" if silent else ("pending" if caption else "skipped")})
        caps.sort(key=lambda c: c["seq"])
        t = 0.0
        for c in caps:  # offsets from the chunks actually received, in order
            c["start"], c["end"] = round(t, 2), round(t + c["duration"], 2)
            t += c["duration"]
        store.write(mid, "captions", caps)
        entry = next(c for c in caps if c["seq"] == seq)
    store.update_meeting(mid, duration=round(t, 1), status_note=f"Recording, {int(t // 60)} min so far")
    if caption and not silent:
        _pool.submit(_caption, svc, mid, seq, wav_bytes, meta.get("language_code") or "unknown")
    return entry


def _set_caption(mid: str, seq: int, **fields) -> None:
    with _cap_lock:
        caps = store.read(mid, "captions", [])
        for c in caps:
            if c["seq"] == seq:
                c.update(fields)
        store.write(mid, "captions", caps)


def _caption(svc, mid: str, seq: int, wav_bytes: bytes, language_code: str) -> None:
    try:
        text, lang = svc.transcribe_short(wav_bytes, f"live-{seq}.wav", language_code)
        _set_caption(mid, seq, text=(text or "").strip(), status="done", language=lang)
    except Exception as e:
        print(f"[sabhasaar] live caption {seq} failed: {e}", flush=True)
        _set_caption(mid, seq, status="failed", error=str(e)[:200])


def captions(mid: str) -> list[dict]:
    return store.read(mid, "captions", [])


# ---------------- stopping ----------------
def stop(svc, mid: str) -> dict:
    meta = store.read(mid, "meeting")
    if not meta:
        raise KeyError(mid)
    if meta.get("status") != "live":
        return meta
    job = _links.get(mid)
    if job:  # the recorder thread finishes the meeting itself
        job["stop"].set()
        if job.get("proc"):
            job["proc"].kill()
        return store.update_meeting(mid, status_note="Stopping the stream recording")
    return finalize(svc, mid)


def finalize(svc, mid: str) -> dict:
    """Join the chunks into one recording and hand it to the normal pipeline."""
    d = store.mdir(mid)
    chunks = sorted((d / "live").glob("chunk-*.wav")) if (d / "live").exists() else []
    total = 0
    out = d / "original.wav"
    if chunks:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            for c in chunks:
                with wave.open(str(c)) as r:
                    frames = r.readframes(r.getnframes())
                    total += len(frames)
                    w.writeframes(frames)
    if total < RATE * 2:  # under a second
        return store.update_meeting(mid, status="failed",
                                    status_note="Nothing was recorded. Check that the tab or microphone had sound.")
    shutil.rmtree(d / "live", ignore_errors=True)
    meta = store.update_meeting(mid, status="queued", status_note="Recording finished, making the minutes",
                                duration=round(total / 2 / RATE, 1))
    threading.Thread(target=pipeline.process_meeting, args=(svc, mid, out), daemon=True).start()
    return meta


# ---------------- recording a link on the server ----------------
def check_url(url: str) -> str:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise LiveError("Paste a full http or https link to the stream")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise LiveError("That website could not be found")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise LiveError("That link points to a private address")
    return url


def resolve_stream(url: str) -> str:
    """Direct media links go straight to ffmpeg; pages such as YouTube go through yt-dlp first."""
    if urlparse(url).path.lower().endswith(DIRECT_MEDIA):
        return url
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "-g", "--no-playlist",
                            "--no-warnings", url], capture_output=True, text=True, timeout=90)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("http")]
        if lines:
            return lines[0]
        if r.stderr.strip():
            print(f"[sabhasaar] yt-dlp: {r.stderr.strip()[:300]}", flush=True)
    except Exception as e:
        print(f"[sabhasaar] yt-dlp not usable: {e}", flush=True)
    return url  # let ffmpeg try the link as it is


def start_link(svc, mid: str, url: str) -> None:
    job = {"stop": threading.Event(), "proc": None}
    _links[mid] = job
    threading.Thread(target=_record_link, args=(svc, mid, url, job), daemon=True).start()


def _record_link(svc, mid: str, url: str, job: dict) -> None:
    seq, err = 0, ""
    try:
        if not shutil.which("ffmpeg"):
            raise LiveError("Recording from a link needs ffmpeg on the server")
        store.update_meeting(mid, status_note="Connecting to the stream")
        src = resolve_stream(url)
        if job["stop"].is_set():
            raise LiveError("Stopped before the stream started")
        reconnect = (["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
                     if src.startswith(("http://", "https://")) else [])
        proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", *reconnect, "-i", src, "-vn", "-ac", "1", "-ar", str(RATE),
             "-t", str(MAX_SECONDS), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        job["proc"] = proc
        need = RATE * 2 * CHUNK_SECONDS
        buf, t0 = b"", time.time()
        while not job["stop"].is_set():
            data = proc.stdout.read(16384)
            if not data:
                break
            buf += data
            while len(buf) >= need:
                audio_s = (seq + 1) * CHUNK_SECONDS
                # a live stream arrives in real time; a finished recording arrives much faster, and then
                # captions are skipped (the full transcript comes from the batch job anyway)
                realtime = audio_s <= (time.time() - t0) * 2 + 60
                add_chunk(svc, mid, seq, pcm_to_wav(buf[:need]), caption=realtime)
                seq, buf = seq + 1, buf[need:]
        if len(buf) >= RATE * 2:
            add_chunk(svc, mid, seq, pcm_to_wav(buf), caption=True)
            seq += 1
        proc.kill()
        err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()[-300:]
    except Exception as e:
        traceback.print_exc()
        err = str(e)
    finally:
        _links.pop(mid, None)
    if seq == 0:
        store.update_meeting(mid, status="failed",
                             status_note=f"Could not record that link. {err or 'No audio came through.'}")
        return
    finalize(svc, mid)
