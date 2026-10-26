from __future__ import annotations

import io
import json
import mimetypes
from contextlib import asynccontextmanager
import shutil
import threading
from pathlib import Path

import segno
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, demo, live, pipeline, prompts, store
from .sarvam_service import SarvamError, SarvamService

@asynccontextmanager
async def lifespan(_app):
    demo.seed_demo()
    yield


app = FastAPI(title="SabhaSaar", lifespan=lifespan)

# Some Windows machines map .js/.css to the wrong type in the registry, which stops the page loading
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
STATIC = config.ROOT / "static"

_svc = None


def get_service():
    """Swappable in tests."""
    global _svc
    if not config.has_key():
        raise HTTPException(400, "Add your SARVAM_API_KEY to the .env file and restart to use this.")
    if _svc is None:
        _svc = SarvamService(config.SARVAM_API_KEY)
    return _svc


def _meeting_or_404(mid: str) -> dict:
    try:
        m = store.read(mid, "meeting")
    except KeyError:
        m = None
    if not m:
        raise HTTPException(404, "Meeting not found")
    return m


def _err(e: Exception):
    if isinstance(e, HTTPException):
        raise e
    raise HTTPException(502 if isinstance(e, SarvamError) else 500, str(e)[:400])


# ---------------- config ----------------
@app.get("/api/config")
def get_config():
    return {
        "has_key": config.has_key(),
        "languages": config.LANGUAGES,
        "meeting_types": {k: {"label": v["label"], "decisions": v["decisions"], "actions": v["actions"],
                              "extras": {e[0]: e[1] for e in v["extras"]}, "disclaimer": v.get("disclaimer")}
                          for k, v in prompts.MEETING_TYPES.items()},
        "models": {"stt": config.STT_MODEL, "llm": config.LLM_MODEL, "tts": config.TTS_MODEL},
    }


# ---------------- meetings ----------------
@app.get("/api/meetings")
def list_meetings():
    return store.list_meetings()


def _meeting_record(mid, title, body, meeting_type, date, language_code, num_speakers, minutes_language,
                    keyterms, status, source_name, **extra) -> dict:
    return {
        "id": mid, "title": title.strip(), "body": body.strip(),
        "meeting_type": meeting_type if meeting_type in prompts.MEETING_TYPES else "general",
        "date": date, "language_code": language_code or "unknown",
        "num_speakers": int(num_speakers) if str(num_speakers).strip().isdigit() else None,
        "minutes_language": minutes_language if minutes_language in config.LANGUAGES else "en-IN",
        "keyterms": keyterms, "status": status,
        "status_note": "Recording" if status == "live" else "Waiting to start",
        "speakers": {}, "created_at": store.now(), "updated_at": store.now(),
        "source_name": source_name, **extra,
    }


@app.post("/api/meetings")
async def create_meeting(
    file: UploadFile = File(...),
    title: str = Form(""),
    body: str = Form(""),
    meeting_type: str = Form("gram_sabha"),
    date: str = Form(""),
    language_code: str = Form("unknown"),
    num_speakers: str = Form(""),
    minutes_language: str = Form("en-IN"),
    keyterms: str = Form(""),
):
    svc = get_service()
    mid = store.new_id()
    d = store.mdir(mid)
    d.mkdir(parents=True)
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    original = d / f"original{suffix}"
    with open(original, "wb") as f:
        shutil.copyfileobj(file.file, f)
    meeting = _meeting_record(mid, title, body, meeting_type, date, language_code, num_speakers,
                              minutes_language, keyterms, status="queued", source_name=file.filename)
    store.write(mid, "meeting", meeting)
    threading.Thread(target=pipeline.process_meeting, args=(svc, mid, original), daemon=True).start()
    return meeting


@app.get("/api/meetings/{mid}")
def get_meeting(mid: str):
    m = _meeting_or_404(mid)
    return {
        "meeting": m,
        "transcript": {"segments": pipeline.meeting_segments(mid)},
        "minutes": store.read(mid, "minutes"),
        "qa": store.read(mid, "qa", []),
        "captions": live.captions(mid) if m.get("status") == "live" else [],
        "others_in_body": sum(1 for x in store.list_meetings()
                              if m.get("body") and x.get("body") == m.get("body") and x["id"] != mid),
    }


@app.get("/api/meetings/{mid}/status")
def meeting_status(mid: str):
    m = _meeting_or_404(mid)
    return {"status": m["status"], "status_note": m.get("status_note", "")}


@app.get("/api/meetings/{mid}/audio")
def meeting_audio(mid: str):
    m = _meeting_or_404(mid)
    if not m.get("audio_file"):
        raise HTTPException(404, "No audio for this meeting")
    return FileResponse(store.mdir(mid) / m["audio_file"])


class Rename(BaseModel):
    speakers: dict[str, str]


@app.patch("/api/meetings/{mid}/speakers")
def rename_speakers(mid: str, body: Rename):
    m = _meeting_or_404(mid)
    speakers = m.get("speakers") or {}
    for sid, name in body.speakers.items():
        if sid in speakers and name.strip():
            speakers[sid]["name"] = name.strip()[:60]
            speakers[sid]["named_by"] = "person"
    return store.update_meeting(mid, speakers=speakers)


@app.post("/api/meetings/{mid}/rebuild")
def rebuild(mid: str):
    """Re-run whatever failed (or re-write minutes after renaming speakers)."""
    m = _meeting_or_404(mid)
    svc = get_service()
    original = next(store.mdir(mid).glob("original.*"), None)
    if not (store.mdir(mid) / "transcript.json").exists() and original is None:
        raise HTTPException(400, "Nothing to rebuild from")
    store.update_meeting(mid, status="queued", status_note="Starting again")
    if (store.mdir(mid) / "transcript.json").exists():
        threading.Thread(target=_safe_minutes, args=(svc, mid), daemon=True).start()
    else:
        threading.Thread(target=pipeline.process_meeting, args=(svc, mid, original), daemon=True).start()
    return {"ok": True, "status": "queued", "title": m.get("title")}


def _safe_minutes(svc, mid):
    try:
        pipeline.build_minutes(svc, mid)
    except Exception as e:
        store.update_meeting(mid, status="failed", status_note=str(e)[:400])


# ---------------- for people who missed it ----------------
class Ask(BaseModel):
    question: str
    language: str | None = "auto"
    scope: str = "meeting"


@app.post("/api/meetings/{mid}/ask")
def ask(mid: str, body: Ask):
    _meeting_or_404(mid)
    if not body.question.strip():
        raise HTTPException(400, "Type or say a question first")
    svc = get_service()
    try:
        return pipeline.ask(svc, mid, body.question.strip()[:1000], body.language, body.scope)
    except Exception as e:
        _err(e)


@app.post("/api/meetings/{mid}/ask-voice")
async def ask_voice(mid: str, audio: UploadFile = File(...), language: str = Form("auto"),
                    scope: str = Form("meeting")):
    _meeting_or_404(mid)
    svc = get_service()
    try:
        data = await audio.read()
        text, detected = svc.transcribe_short(data, audio.filename or "question.webm")
        if not text.strip():
            raise HTTPException(400, "Could not hear a question. Try again closer to the mic.")
        lang = language if language and language != "auto" else (detected or None)
        result = pipeline.ask(svc, mid, text.strip(), lang, scope)
        result["heard_language"] = detected
        return result
    except Exception as e:
        _err(e)


class Speak(BaseModel):
    text: str
    language: str = "hi-IN"


@app.post("/api/meetings/{mid}/speak")
def speak(mid: str, body: Speak):
    _meeting_or_404(mid)
    svc = get_service()
    try:
        wav = pipeline.speak_answer(svc, mid, body.text[:2400], body.language)
        return Response(wav, media_type="audio/wav")
    except Exception as e:
        _err(e)


class BriefReq(BaseModel):
    language: str = "hi-IN"


@app.post("/api/meetings/{mid}/brief")
def make_brief(mid: str, body: BriefReq):
    m = _meeting_or_404(mid)
    if m.get("status") != "ready":
        raise HTTPException(400, "Minutes are not ready yet")
    cached = store.mdir(mid) / "briefs" / f"{body.language}.json"
    fresh = False
    if cached.exists():
        try:
            fresh = json.loads(cached.read_text(encoding="utf-8")).get("version") == pipeline.BRIEF_VERSION
        except Exception:
            fresh = False
    svc = None if fresh else get_service()  # an up-to-date saved brief works without a key
    try:
        out = pipeline.brief(svc, mid, body.language)
        if out.get("audio"):
            out["audio_url"] = f"/api/meetings/{mid}/brief-audio/{body.language}"
        return out
    except Exception as e:
        _err(e)


@app.get("/api/meetings/{mid}/brief-audio/{lang}")
def brief_audio(mid: str, lang: str):
    _meeting_or_404(mid)
    p = store.mdir(mid) / "briefs" / f"{lang}.wav"
    if lang not in config.LANGUAGES or not p.exists():
        raise HTTPException(404, "No audio brief yet")
    return FileResponse(p, media_type="audio/wav")


@app.get("/api/meetings/{mid}/qr")
def qr(mid: str, request: Request):
    _meeting_or_404(mid)
    url = f"{str(request.base_url).rstrip('/')}/#/m/{mid}/listen"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=5, dark="#1d2a4a", light="#ffffff", xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml")


@app.get("/api/meetings/{mid}/export.md", response_class=PlainTextResponse)
def export_md(mid: str):
    m = _meeting_or_404(mid)
    minutes = store.read(mid, "minutes") or {}
    segs = store.read(mid, "transcript", {}).get("segments", [])
    mt = prompts.MEETING_TYPES.get(m.get("meeting_type"), prompts.MEETING_TYPES["general"])

    def stamp(refs):
        times = [pipeline.hms(segs[r]["start"]) for r in refs if 0 <= r < len(segs)]
        return f" ({', '.join(times)})" if times else ""

    lines = [f"# {m.get('title') or minutes.get('title')}", "",
             f"{m.get('body') or ''}  {m.get('date') or ''}", "", minutes.get("one_line", ""), ""]
    lines += ["## Summary"] + [f"- {i.get('text')}{stamp(i['refs'])}" for i in minutes.get("summary", [])]
    lines += ["", f"## {mt['decisions']}"] + [
        f"- {i.get('text')} [{i.get('outcome') or ''}]{stamp(i['refs'])}" for i in minutes.get("decisions", [])]
    lines += ["", f"## {mt['actions']}"] + [
        f"- {i.get('task') or i.get('text')} | {i.get('owner') or 'owner not said'} | {i.get('deadline') or 'no date'}{stamp(i['refs'])}"
        for i in minutes.get("actions", [])]
    for key, label, _ in mt["extras"]:
        items = minutes.get("extras", {}).get(key) or []
        if items:
            lines += ["", f"## {label}"] + [f"- {i['text']}{stamp(i['refs'])}" for i in items]
    if minutes.get("open_issues"):
        lines += ["", "## Left unresolved"] + [f"- {i['text']}{stamp(i['refs'])}" for i in minutes["open_issues"]]
    lines += ["", "---", f"Audio fingerprint (SHA-256): {m.get('audio_sha256') or 'n/a'}",
              "Generated by SabhaSaar with Sarvam AI. Check the audio before relying on any point."]
    if mt.get("disclaimer"):
        lines.append(mt["disclaimer"])
    return "\n".join(lines)


# ---------------- live recording ----------------
LIVE_SOURCES = {"tab": "Online meeting (tab audio)", "mic": "Microphone in the room", "link": "Stream link"}


@app.post("/api/live")
def start_live(
    source: str = Form("mic"),
    url: str = Form(""),
    title: str = Form(""),
    body: str = Form(""),
    meeting_type: str = Form("gram_sabha"),
    date: str = Form(""),
    language_code: str = Form("unknown"),
    num_speakers: str = Form(""),
    minutes_language: str = Form("en-IN"),
    keyterms: str = Form(""),
):
    if source not in LIVE_SOURCES:
        raise HTTPException(400, "Unknown recording source")
    svc = get_service()
    if source == "link":
        try:
            url = live.check_url(url)
        except live.LiveError as e:
            raise HTTPException(400, str(e))
    mid = store.new_id()
    store.mdir(mid).mkdir(parents=True)
    meeting = _meeting_record(mid, title, body, meeting_type, date, language_code, num_speakers, minutes_language,
                              keyterms, status="live", source_name=url if source == "link" else LIVE_SOURCES[source],
                              live_source=source, live_started_at=store.now())
    store.write(mid, "meeting", meeting)
    store.write(mid, "captions", [])
    if source == "link":
        live.start_link(svc, mid, url)
    return meeting


@app.post("/api/live/{mid}/chunk")
async def live_chunk(mid: str, audio: UploadFile = File(...), seq: int = Form(...)):
    _meeting_or_404(mid)
    data = await audio.read()
    if len(data) > 8_000_000:
        raise HTTPException(413, "Chunk too large")
    try:
        return live.add_chunk(get_service(), mid, seq, data)
    except live.LiveError as e:
        raise HTTPException(409, str(e))


@app.get("/api/live/{mid}/captions")
def live_captions(mid: str):
    m = _meeting_or_404(mid)
    return {"status": m["status"], "status_note": m.get("status_note", ""), "duration": m.get("duration"),
            "captions": live.captions(mid)}


@app.post("/api/live/{mid}/stop")
def live_stop(mid: str):
    _meeting_or_404(mid)
    try:
        return live.stop(get_service(), mid)
    except Exception as e:
        _err(e)


# ---------------- frontend ----------------
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
