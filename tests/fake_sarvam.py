"""A stand-in for SarvamService so the whole app can run and be tested without a key."""
import io
import json
import math
import struct
import wave


def _tone_wav(seconds=1.0, rate=22050):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 20))) for i in range(int(rate * seconds))))
    return buf.getvalue()


class FakeSarvam:
    def __init__(self):
        self.calls = []

    def transcribe_meeting(self, audio_path, language_code="unknown", num_speakers=None, keyterms=None, mode="transcribe"):
        self.calls.append(("stt_batch", language_code, num_speakers, keyterms))
        entries = [
            {"transcript": "बैठक शुरू करते हैं। आज सड़क पर बात होगी।", "start_time_seconds": 0.0, "end_time_seconds": 4.0, "speaker_id": "0"},
            {"transcript": "सड़क के लिए दो लाख रुपये मंज़ूर करने का प्रस्ताव है।", "start_time_seconds": 4.5, "end_time_seconds": 9.0, "speaker_id": "1"},
            {"transcript": "हाँ,", "start_time_seconds": 9.2, "end_time_seconds": 9.6, "speaker_id": "1"},
            {"transcript": "मैं सहमत हूँ।", "start_time_seconds": 9.8, "end_time_seconds": 11.0, "speaker_id": "1"},
            {"transcript": "प्रस्ताव पास हुआ। काम 30 दिन में शुरू होगा।", "start_time_seconds": 12.0, "end_time_seconds": 16.0, "speaker_id": "0"},
        ]
        return {"transcript": " ".join(e["transcript"] for e in entries), "language_code": "hi-IN",
                "diarized_transcript": {"entries": entries}}

    def transcribe_short(self, audio, filename="q.webm", language_code="unknown"):
        self.calls.append(("stt_short", len(audio), filename, language_code))
        if filename.startswith("live-"):
            n = sum(1 for c in self.calls if c[0] == "stt_short" and str(c[2]).startswith("live-"))
            return f"लाइव हिस्सा {n}: पार्क की सफाई पर चर्चा।", "hi-IN"
        return "सड़क का क्या हुआ?", "hi-IN"

    def chat(self, messages, max_tokens=4096, json_mode=False, reasoning_effort="low"):
        system = messages[0]["content"]
        self.calls.append(("chat", system[:40], len(messages[-1]["content"])))
        if "careful minute-taker" in system:
            return "```json\n" + json.dumps({
                "title": "Road meeting", "date_mentioned": None,
                "one_line": "The road got ₹2 lakh.",
                "summary": [{"text": "Road funding proposed and passed", "refs": ["#1", 3]}],
                "decisions": [{"text": "₹2 lakh for the road", "outcome": "passed", "refs": [1, 3]}],
                "actions": [{"task": "Start road work", "owner": None, "deadline": "30 days", "refs": [3]}],
                "topics": [{"title": "Road", "start_ref": "#0", "summary": "Road money"}],
                "speakers": [{"id": "S0", "suggested_name": "Sarpanch", "role": "Chair", "key_points": "Chaired"},
                             {"id": "1", "suggested_name": None, "role": "Member", "key_points": ["Proposed funds"]}],
                "extras": {"funds": [{"text": "₹2 lakh road", "refs": [1]}], "complaints": "none"},
                "open_issues": [],
            }, ensure_ascii=False) + "\n```"
        if "merge partial minutes" in system:
            return json.dumps({"title": "Merged", "one_line": "merged", "summary": [{"text": "merged", "refs": [0]}]})
        if "pick the refs" in system:
            return json.dumps({"refs": [1]})
        if "help people who could not attend" in system:
            multi = "labelled M1" in system
            return json.dumps({"answer": "सड़क के लिए दो लाख रुपये पास हुए।", "refs": ["M1#1", "M2#2"] if multi else ["#1", "#3", "#99"],
                               "status": "answered", "language_code": "hi-IN", "follow_ups": ["काम कब शुरू होगा?"]},
                              ensure_ascii=False)
        if "Translate the user's text into" in system:
            return "சாலைக்கு இரண்டு லட்சம் ரூபாய் ஒதுக்கப்பட்டது."
        if "spoken briefing" in system:
            return "आज की बैठक में सड़क के लिए दो लाख रुपये पास हुए। काम तीस दिन में शुरू होगा।"
        raise AssertionError("unexpected prompt: " + system[:80])

    def speak(self, text, language_code):
        self.calls.append(("tts", language_code, len(text)))
        return _tone_wav(0.5)
