"""Tiny file-based store: data/meetings/<id>/{meeting,transcript,minutes}.json"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config

_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def mdir(mid: str) -> Path:
    if not mid.isalnum():
        raise KeyError(mid)
    return config.MEETINGS_DIR / mid


def read(mid: str, name: str, default=None):
    p = mdir(mid) / f"{name}.json"
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write(mid: str, name: str, data) -> None:
    d = mdir(mid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / f"{name}.json")


def update_meeting(mid: str, **fields) -> dict:
    with _lock:
        m = read(mid, "meeting")
        if m is None:
            raise KeyError(mid)
        m.update(fields)
        m["updated_at"] = now()
        write(mid, "meeting", m)
        return m


def list_meetings() -> list[dict]:
    out = []
    for d in config.MEETINGS_DIR.iterdir():
        if d.is_dir() and (d / "meeting.json").exists():
            out.append(json.loads((d / "meeting.json").read_text(encoding="utf-8")))
    out.sort(key=lambda m: (m.get("date") or "", m.get("created_at") or ""), reverse=True)
    return out


def append_qa(mid: str, entry: dict) -> None:
    with _lock:
        qa = read(mid, "qa", [])
        qa.append(entry)
        write(mid, "qa", qa)
