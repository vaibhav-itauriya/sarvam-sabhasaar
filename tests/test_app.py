"""Run: python -m pytest -q   (uses a fake Sarvam service, no key or network needed)"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sabhasaar-test-")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, main, pipeline, store  # noqa: E402
from app.sarvam_service import join_wavs, parse_json, split_for_tts  # noqa: E402
from tests.fake_sarvam import FakeSarvam, _tone_wav  # noqa: E402

FAKE = FakeSarvam()


@pytest.fixture(scope="module")
def client():
    main.get_service = lambda: FAKE
    config.has_key = lambda: True
    with TestClient(main.app) as c:
        yield c


def make_audio() -> bytes:
    return _tone_wav(16.0, rate=16000)  # pure Python, so tests run without ffmpeg


def wait_ready(client, mid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/meetings/{mid}/status").json()
        if st["status"] in ("ready", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError("timed out")


def upload(client, body="Rampur Gram Panchayat", date="2026-10-20"):
    r = client.post("/api/meetings", files={"file": ("meeting.wav", make_audio(), "audio/wav")},
                    data={"meeting_type": "gram_sabha", "body": body, "date": date, "language_code": "hi-IN",
                          "num_speakers": "2", "keyterms": "MGNREGA, Rampur"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_demo_seeded(client):
    r = client.get("/api/meetings/demogramsabha").json()
    assert r["meeting"]["status"] == "ready" and len(r["transcript"]["segments"]) == 30
    assert len(r["qa"]) == 3


def test_full_pipeline(client):
    mid = upload(client)
    st = wait_ready(client, mid)
    assert st["status"] == "ready", st
    data = client.get(f"/api/meetings/{mid}").json()
    m, segs, minutes = data["meeting"], data["transcript"]["segments"], data["minutes"]
    # tiny same-speaker pieces were merged: 5 entries -> 3 segments
    assert len(segs) == 3 and segs[1]["text"].endswith("मैं सहमत हूँ।")
    assert m["title"] == "Road meeting" and m["duration"] and len(m["audio_sha256"]) == 64
    assert m["speakers"]["0"]["name"] == "Sarpanch" and m["speakers"]["1"]["name"] == "Speaker 2"
    assert minutes["summary"][0]["refs"] == [1, 3] and minutes["topics"][0]["start_ref"] == 0
    assert minutes["speakers"][0]["id"] == "0" and minutes["extras"]["complaints"][0]["text"] == "none"
    assert [c for c in FAKE.calls if c[0] == "stt_batch"][-1][3] == ["MGNREGA", "Rampur"]
    assert client.get(f"/api/meetings/{mid}/audio").status_code == 200

    # rename then ask
    r = client.patch(f"/api/meetings/{mid}/speakers", json={"speakers": {"1": "Imran Ali"}})
    assert r.json()["speakers"]["1"]["named_by"] == "person"
    a = client.post(f"/api/meetings/{mid}/ask", json={"question": "सड़क का क्या हुआ?", "language": "auto"}).json()
    assert a["status"] == "answered" and [x["seg"] for x in a["refs"]] == [1]
    assert all(x["meeting_id"] == mid for x in a["refs"])  # out-of-range refs #3 and #99 were dropped

    v = client.post(f"/api/meetings/{mid}/ask-voice", files={"audio": ("q.webm", b"abc", "audio/webm")},
                    data={"language": "auto"}).json()
    assert v["question"] == "सड़क का क्या हुआ?" and v["heard_language"] == "hi-IN"

    b = client.post(f"/api/meetings/{mid}/brief", json={"language": "ta-IN"}).json()
    assert b["audio_url"].endswith("/ta-IN") and client.get(b["audio_url"]).status_code == 200
    n_tts = sum(1 for c in FAKE.calls if c[0] == "tts")
    client.post(f"/api/meetings/{mid}/brief", json={"language": "ta-IN"})  # cached
    assert sum(1 for c in FAKE.calls if c[0] == "tts") == n_tts

    assert client.post(f"/api/meetings/{mid}/speak", json={"text": "नमस्ते", "language": "hi-IN"}).headers["content-type"] == "audio/wav"
    assert "image/svg" in client.get(f"/api/meetings/{mid}/qr").headers["content-type"]
    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "Resolutions passed" in md and "(00:00:04" in md

    # rewrite minutes keeps the person's name
    client.post(f"/api/meetings/{mid}/rebuild")
    wait_ready(client, mid)
    assert client.get(f"/api/meetings/{mid}").json()["meeting"]["speakers"]["1"]["name"] == "Imran Ali"
    assert len(store.read(mid, "qa")) == 2


def test_ask_across_body(client):
    a_id = upload(client, body="Sitapur Ward 4", date="2026-09-01")
    b_id = upload(client, body="Sitapur Ward 4", date="2026-10-01")
    wait_ready(client, a_id), wait_ready(client, b_id)
    assert client.get(f"/api/meetings/{b_id}").json()["others_in_body"] == 1
    ans = client.post(f"/api/meetings/{b_id}/ask", json={"question": "road?", "scope": "body"}).json()
    targets = {r["meeting_id"] for r in ans["refs"]}
    assert targets == {a_id, b_id}
    assert any(r["label"].startswith("M1") for r in ans["refs"])


def test_long_meeting_paths(client, monkeypatch):
    mid = upload(client, body="Long body")
    wait_ready(client, mid)
    monkeypatch.setattr(config, "FULL_CONTEXT_CHARS", 50)
    monkeypatch.setattr(config, "CHUNK_CHARS", 60)
    pipeline.build_minutes(FAKE, mid)
    assert store.read(mid, "minutes")["title"] == "Merged"
    ans = client.post(f"/api/meetings/{mid}/ask", json={"question": "x"}).json()
    assert ans["answer"]


def test_failure_is_visible(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Saaras batch job failed: bad audio")
    monkeypatch.setattr(FAKE, "transcribe_meeting", boom)
    mid = upload(client, body="x")
    st = wait_ready(client, mid)
    assert st["status"] == "failed" and "bad audio" in st["status_note"]


def test_helpers():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('blah {"a": {"b": 2}} blah') == {"a": {"b": 2}}
    text = "वाक्य एक। " * 600
    chunks = split_for_tts(text, 1800)
    assert all(len(c) <= 1800 for c in chunks) and "".join(chunks).replace(" ", "") == text.replace(" ", "")
    w = join_wavs([_tone_wav(0.5), _tone_wav(0.25)])
    import io
    import wave
    with wave.open(io.BytesIO(w)) as r:
        assert abs(r.getnframes() / r.getframerate() - 0.75) < 0.01
    assert pipeline.hms(3725) == "01:02:05"


def test_chat_retries_empty_answer():
    from types import SimpleNamespace as NS
    from app.sarvam_service import SarvamService
    replies = ["", "", '{"answer": "ok"}']
    seen = []

    class Client:
        class chat:
            @staticmethod
            def completions(**kw):
                seen.append(kw)
                return NS(choices=[NS(message=NS(content=replies[len(seen) - 1]), finish_reason="length")])

    svc = SarvamService.__new__(SarvamService)
    svc.client = Client
    assert svc.chat([{"role": "user", "content": "x"}], max_tokens=2000, json_mode=True) == '{"answer": "ok"}'
    assert seen[1]["max_tokens"] == 6000 and "response_format" not in seen[2]


def test_plain_text_answer_is_kept(client, monkeypatch):
    mid = upload(client, body="Plain")
    wait_ready(client, mid)
    real = FAKE.chat
    monkeypatch.setattr(FAKE, "chat", lambda m, **k: "The road got two lakh rupees (#1)."
                        if "help people who could not attend" in m[0]["content"] else real(m, **k))
    a = client.post(f"/api/meetings/{mid}/ask", json={"question": "road?"}).json()
    assert a["answer"] == "The road got two lakh rupees." and [r["seg"] for r in a["refs"]] == [1]


def test_last_json_object():
    from app.sarvam_service import last_json_object
    t = 'thinking {"draft": 1} more thoughts... final: {"answer": "x", "refs": ["#1"], "meta": {"a": 1}} done'
    assert json.loads(last_json_object(t))["answer"] == "x"
    assert last_json_object("no json here") is None
