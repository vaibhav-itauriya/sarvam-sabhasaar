"""Meeting processing: audio -> speaker transcript -> minutes, plus ask and brief."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import traceback
from pathlib import Path

from . import config, prompts, store
from .sarvam_service import SarvamError, parse_json


# ---------------- audio ----------------
def prepare_audio(src: Path, dest_dir: Path) -> tuple[Path, float | None]:
    """Convert anything (webm, m4a, mp4 video, wav) to 16 kHz mono mp3 for Saaras + browser playback."""
    out = dest_dir / "audio.mp3"
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "48k", str(out)],
            check=True,
        )
    else:  # no ffmpeg: send the original and hope the format is supported
        out = dest_dir / f"audio{src.suffix.lower()}"
        if src != out:
            shutil.copy(src, out)
    return out, probe_duration(out)


def probe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        if path.suffix.lower() == ".wav":
            import wave
            try:
                with wave.open(str(path)) as w:
                    return round(w.getnframes() / w.getframerate(), 1)
            except Exception:
                return None
        return None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
        return round(float(r.stdout.strip()), 1)
    except Exception:
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------- transcript ----------------
def segments_from_sarvam(raw: dict) -> list[dict]:
    """Turn Saaras batch output into [{id, start, end, speaker, text}], merging tiny same-speaker pieces."""
    entries = (raw.get("diarized_transcript") or {}).get("entries") or []
    segs: list[dict] = []
    if entries:
        for e in entries:
            segs.append({
                "start": float(e.get("start_time_seconds") or 0),
                "end": float(e.get("end_time_seconds") or 0),
                "speaker": str(e.get("speaker_id", "0")),
                "text": (e.get("transcript") or "").strip(),
            })
    else:
        ts = raw.get("timestamps") or {}
        words = ts.get("words") or ts.get("chunks") or []
        starts, ends = ts.get("start_time_seconds") or [], ts.get("end_time_seconds") or []
        for i, w in enumerate(words):
            segs.append({"start": float(starts[i]) if i < len(starts) else 0.0,
                         "end": float(ends[i]) if i < len(ends) else 0.0,
                         "speaker": "0", "text": str(w).strip()})
        if not segs and raw.get("transcript"):
            segs.append({"start": 0.0, "end": 0.0, "speaker": "0", "text": raw["transcript"].strip()})

    merged: list[dict] = []
    for s in segs:
        if not s["text"]:
            continue
        prev = merged[-1] if merged else None
        if (prev and prev["speaker"] == s["speaker"] and s["start"] - prev["end"] < 1.0
                and len(prev["text"]) + len(s["text"]) < 450):
            prev["text"] = f"{prev['text']} {s['text']}"
            prev["end"] = s["end"]
        else:
            merged.append(dict(s))
    for i, s in enumerate(merged):
        s["id"] = i
    return merged


def hms(seconds: float) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def speaker_label(speakers: dict, sid: str) -> str:
    sp = speakers.get(sid) or {}
    return f"S{sid} {sp.get('name') or ''}".strip()


def format_transcript(segments: list[dict], speakers: dict, prefix: str = "") -> str:
    return "\n".join(
        f"[{prefix}#{s['id']} | {hms(s['start'])} | {speaker_label(speakers, s['speaker'])}] {s['text']}"
        for s in segments
    )


def chunk_lines(text: str, limit: int) -> list[str]:
    chunks, cur, size = [], [], 0
    for line in text.split("\n"):
        if size + len(line) > limit and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


# ---------------- minutes ----------------
def lang_name(code: str | None) -> str:
    return config.LANGUAGE_NAMES_EN.get(code or "en-IN", "English")


def make_minutes(svc, segments: list[dict], speakers: dict, meta: dict) -> dict:
    lname = lang_name(meta.get("minutes_language"))
    text = format_transcript(segments, speakers)
    if len(text) <= config.FULL_CONTEXT_CHARS:
        reply = svc.chat(prompts.minutes_messages(text, meta, lname), max_tokens=8000, json_mode=True)
        return normalise_minutes(parse_json(reply))
    parts = chunk_lines(text, config.CHUNK_CHARS)
    partials = []
    for i, part in enumerate(parts, 1):
        note = f"This is part {i} of {len(parts)} of a long meeting. Summarise only this part.\n"
        reply = svc.chat(prompts.minutes_messages(part, meta, lname, note), max_tokens=6000, json_mode=True)
        partials.append(parse_json(reply))
    reply = svc.chat(prompts.merge_minutes_messages(partials, meta, lname), max_tokens=8000, json_mode=True)
    return normalise_minutes(parse_json(reply))


def _refs(v) -> list[int]:
    out = []
    for r in v or []:
        m = re.search(r"\d+", str(r))
        if m:
            out.append(int(m.group()))
    return out


def normalise_minutes(m: dict) -> dict:
    """Make the model's JSON safe for the UI: lists are lists, refs are ints."""
    def items(key):
        val = m.get(key) or []
        if isinstance(val, dict):
            val = [val]
        res = []
        for it in val:
            if isinstance(it, str):
                it = {"text": it}
            it = dict(it)
            it["refs"] = _refs(it.get("refs"))
            res.append(it)
        return res

    out = {
        "title": m.get("title") or "",
        "date_mentioned": m.get("date_mentioned"),
        "one_line": m.get("one_line") or "",
        "summary": items("summary"),
        "decisions": items("decisions"),
        "actions": items("actions"),
        "open_issues": items("open_issues"),
        "topics": [],
        "speakers": [],
        "extras": {},
    }
    for t in m.get("topics") or []:
        if isinstance(t, dict):
            sr = _refs([t.get("start_ref")])
            out["topics"].append({"title": t.get("title") or "", "summary": t.get("summary") or "",
                                  "start_ref": sr[0] if sr else None})
    for s in m.get("speakers") or []:
        if isinstance(s, dict) and s.get("id") is not None:
            sid = re.sub(r"^S", "", str(s["id"]).strip())
            kp = s.get("key_points") or []
            out["speakers"].append({"id": sid, "suggested_name": s.get("suggested_name"),
                                    "role": s.get("role") or "",
                                    "key_points": kp if isinstance(kp, list) else [str(kp)]})
    extras = m.get("extras") or {}
    if isinstance(extras, dict):
        for k, v in extras.items():
            v = v if isinstance(v, list) else [v]
            out["extras"][k] = [
                {"text": (it.get("text") if isinstance(it, dict) else str(it)) or "",
                 "refs": _refs(it.get("refs") if isinstance(it, dict) else [])}
                for it in v if it
            ]
    return out


# ---------------- background job ----------------
def process_meeting(svc, mid: str, original: Path) -> None:
    d = store.mdir(mid)
    try:
        meta = store.read(mid, "meeting")
        if not (d / "transcript.json").exists():
            store.update_meeting(mid, status="preparing", status_note="Converting audio")
            audio, duration = prepare_audio(original, d)
            store.update_meeting(mid, audio_file=audio.name, duration=duration, audio_sha256=sha256(audio),
                                 status="transcribing",
                                 status_note="Saaras is transcribing and separating speakers")
            keyterms = [k.strip() for k in (meta.get("keyterms") or "").split(",") if k.strip()]
            raw = svc.transcribe_meeting(audio, meta.get("language_code") or "unknown",
                                         meta.get("num_speakers"), keyterms)
            segments = segments_from_sarvam(raw)
            if not segments:
                raise SarvamError("No speech found in the audio")
            store.write(mid, "transcript", {"segments": segments, "language_code": raw.get("language_code")})
            speakers = {}
            for s in segments:
                if s["speaker"] not in speakers:
                    speakers[s["speaker"]] = {"name": f"Speaker {len(speakers) + 1}", "role": "", "named_by": "auto"}
            store.update_meeting(mid, speakers=speakers, detected_language=raw.get("language_code"))
        build_minutes(svc, mid)
    except Exception as e:  # keep the error visible in the UI
        traceback.print_exc()
        store.update_meeting(mid, status="failed", status_note=str(e)[:400])


def build_minutes(svc, mid: str) -> None:
    store.update_meeting(mid, status="summarising", status_note="Sarvam-105B is writing the minutes")
    meta = store.read(mid, "meeting")
    segments = store.read(mid, "transcript")["segments"]
    minutes = make_minutes(svc, segments, meta.get("speakers") or {}, meta)
    store.write(mid, "minutes", minutes)
    # use the model's speaker guesses unless a person already renamed them
    speakers = meta.get("speakers") or {}
    for sp in minutes.get("speakers", []):
        cur = speakers.get(sp["id"])
        if cur and cur.get("named_by") == "auto":
            if sp.get("suggested_name"):
                cur["name"] = sp["suggested_name"]
                cur["named_by"] = "model"
            if sp.get("role") and sp["role"].strip().lower() not in ("a speaker", "speaker", "unknown", "none", "n/a"):
                cur["role"] = sp["role"]
    fields = dict(status="ready", status_note="", speakers=speakers)
    if not meta.get("title") and minutes.get("title"):
        fields["title"] = minutes["title"]
    store.update_meeting(mid, **fields)
    # briefs depend on minutes, so drop any stale ones
    shutil.rmtree(store.mdir(mid) / "briefs", ignore_errors=True)


# ---------------- ask ----------------
def _context_for_meeting(svc, mid: str, question: str, prefix: str = "") -> str:
    meta = store.read(mid, "meeting")
    segments = store.read(mid, "transcript", {}).get("segments", [])
    minutes = store.read(mid, "minutes", {})
    speakers = meta.get("speakers") or {}
    header = (f"=== {prefix or 'Meeting'}: {meta.get('title')} | {meta.get('body') or ''} | "
              f"date {meta.get('date') or 'unknown'} ===\n")
    full = format_transcript(segments, speakers, prefix)
    if len(full) <= config.FULL_CONTEXT_CHARS:
        return header + "MINUTES:\n" + json.dumps(minutes, ensure_ascii=False) + "\nTRANSCRIPT:\n" + full
    # long meeting: let the model use the minutes as an index, then pull those moments
    picked = set()
    try:
        refs = _refs(parse_json(svc.chat(prompts.pick_refs_messages(question, minutes), max_tokens=800,
                                          json_mode=True)).get("refs"))
    except Exception:
        refs = []
    for r in refs or [s for item in minutes.get("summary", []) for s in item.get("refs", [])]:
        picked.update(range(max(0, r - 6), r + 7))
    chosen = [s for s in segments if s["id"] in picked]
    return (header + "MINUTES:\n" + json.dumps(minutes, ensure_ascii=False) +
            "\nRELEVANT TRANSCRIPT EXCERPTS:\n" + format_transcript(chosen, speakers, prefix))


def ask(svc, mid: str, question: str, language: str | None = None, scope: str = "meeting") -> dict:
    meta = store.read(mid, "meeting")
    if meta.get("status") != "ready":
        raise SarvamError("This meeting is still being processed")
    if not language or language == "auto":
        language = detect_script_lang(question)  # None for English or Hinglish, decided by the model
    lang_hint = lang_name(language) if language else None
    history = store.read(mid, "qa", [])
    if scope == "body" and meta.get("body"):
        same = [m for m in store.list_meetings()
                if m.get("body") == meta.get("body") and m.get("status") == "ready"]
        same.sort(key=lambda m: m.get("date") or "")
        labels = {m["id"]: f"M{i + 1}" for i, m in enumerate(same)}
        context = "\n\n".join(_context_for_meeting(svc, m["id"], question, labels[m["id"]]) for m in same)
        if len(context) > config.FULL_CONTEXT_CHARS:  # many meetings: minutes only
            context = "\n\n".join(
                f"=== {labels[m['id']]}: {m.get('title')} | date {m.get('date')} ===\nMINUTES:\n"
                + json.dumps(store.read(m["id"], "minutes", {}), ensure_ascii=False) for m in same)
        scope_note = (f"You have records of {len(same)} meetings of {meta.get('body')}, labelled M1, M2... "
                      "in date order. Refs look like M2#14. When things changed between meetings, say so. ")
        history = []
    else:
        labels = {mid: ""}
        context = _context_for_meeting(svc, mid, question)
        scope_note = ""
    reply = svc.chat(prompts.answer_messages(question, context, scope_note, history, lang_hint,
                                             meta.get("meeting_type")), max_tokens=4000, json_mode=True)
    try:
        data = parse_json(reply)
        if not isinstance(data, dict) or not data.get("answer"):
            raise SarvamError("no answer field")
    except Exception:  # model replied in plain text: still show it, and keep any #refs it wrote
        data = {"answer": re.sub(r"\s*\(?(M\d+)?#\d+\)?", "", reply).strip(),
                "refs": re.findall(r"(?:M\d+)?#\d+", reply)}
    inv = {v: k for k, v in labels.items()}
    refs = []
    for r in data.get("refs") or []:
        m = re.match(r"\s*(M\d+)?\s*#?\s*(\d+)", str(r))
        if not m:
            continue
        target = inv.get(m.group(1) or "", mid)
        seg = _segment(target, int(m.group(2)))
        if seg:
            refs.append({"meeting_id": target, "seg": seg["id"], "start": seg["start"],
                         "label": hms(seg["start"]) if target == mid else f"{labels[target]} {hms(seg['start'])}"})
    if language:
        data["answer"] = ensure_language(svc, data.get("answer") or "", language)
    answer = {
        "question": question,
        "answer": data.get("answer") or "",
        "status": data.get("status") or "answered",
        "language_code": answer_language(data, language),
        "refs": refs,
        "follow_ups": (data.get("follow_ups") or [])[:3],
        "scope": scope,
        "asked_at": store.now(),
    }
    store.append_qa(mid, answer)
    return answer


def answer_language(data: dict, language: str | None) -> str:
    """Language code for reading the answer aloud, trusting the actual script over the model's label."""
    text = data.get("answer") or ""
    by_script = detect_script_lang(text)
    if by_script:
        claimed = data.get("language_code")
        same_script = claimed in SCRIPT_RANGES and SCRIPT_RANGES[claimed] == SCRIPT_RANGES[by_script]
        return claimed if same_script else (language if language in SCRIPT_RANGES else by_script)
    claimed = data.get("language_code") or "en-IN"
    return claimed if claimed in ("en-IN", "hi-IN") else "en-IN"  # Latin text: English, or Hinglish for the Hindi voice


def _segment(mid: str, seg_id: int) -> dict | None:
    segs = store.read(mid, "transcript", {}).get("segments", [])
    return segs[seg_id] if 0 <= seg_id < len(segs) else None


# ---------------- language checks ----------------
SCRIPT_RANGES = {
    "hi-IN": (0x0900, 0x097F), "mr-IN": (0x0900, 0x097F), "bn-IN": (0x0980, 0x09FF), "pa-IN": (0x0A00, 0x0A7F),
    "gu-IN": (0x0A80, 0x0AFF), "od-IN": (0x0B00, 0x0B7F), "ta-IN": (0x0B80, 0x0BFF), "te-IN": (0x0C00, 0x0C7F),
    "kn-IN": (0x0C80, 0x0CFF), "ml-IN": (0x0D00, 0x0D7F),
}


def script_share(text: str, lang: str) -> float:
    """Share of letters in text written in lang's script (Latin for English)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    if lang == "en-IN":
        return sum(c.isascii() for c in letters) / len(letters)
    lo, hi = SCRIPT_RANGES.get(lang, (0, 0x10FFFF))
    return sum(lo <= ord(c) <= hi for c in letters) / len(letters)


def detect_script_lang(text: str) -> str | None:
    """Language whose script most of text uses, None for Latin (English or Hinglish)."""
    best, share = None, 0.3
    for lang in ("hi-IN", "bn-IN", "pa-IN", "gu-IN", "od-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN"):
        sh = script_share(text, lang)
        if sh > share:
            best, share = lang, sh
    return best


def ensure_language(svc, text: str, language: str | None) -> str:
    """If text is not in language's script, have Sarvam-105B translate it."""
    if not text or not language or language not in config.LANGUAGES or script_share(text, language) >= 0.6:
        return text
    print(f"[sabhasaar] reply was not in {language}, translating", flush=True)
    return svc.chat(prompts.translate_messages(text, lang_name(language)), max_tokens=3000,
                    reasoning_effort="off").strip() or text


# ---------------- spoken brief ----------------
BRIEF_VERSION = 2  # bump to throw away briefs saved by older versions


def brief(svc, mid: str, language: str) -> dict:
    bdir = store.mdir(mid) / "briefs"
    cached = bdir / f"{language}.json"
    if cached.exists():
        old = json.loads(cached.read_text(encoding="utf-8"))
        if old.get("version") == BRIEF_VERSION:
            return old
    meta = store.read(mid, "meeting")
    minutes = store.read(mid, "minutes", {})
    script = svc.chat(prompts.brief_messages(minutes, meta, lang_name(language)), max_tokens=3000,
                      reasoning_effort="off").strip()
    script = ensure_language(svc, script, language)
    out = {"language": language, "script": script, "audio": None, "version": BRIEF_VERSION}
    if language in config.LANGUAGES:
        wav = svc.speak(script, language)
        bdir.mkdir(exist_ok=True)
        (bdir / f"{language}.wav").write_bytes(wav)
        out["audio"] = f"{language}.wav"
    bdir.mkdir(exist_ok=True)
    cached.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def speak_answer(svc, mid: str, text: str, language: str) -> bytes:
    lang = language if language in config.LANGUAGES else "en-IN"
    return svc.speak(text, lang)
