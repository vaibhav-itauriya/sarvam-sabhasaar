# SabhaSaar (सभासार)

Minutes for any public meeting, in any Indian language, where every point is stamped with the moment it was said. People who missed the meeting can listen to a one-minute summary in their own language or ask questions by voice.

Works for Gram Sabha and Panchayat meetings, Lok Sabha, Rajya Sabha and Vidhan Sabha proceedings, court and tribunal hearings, ward committees and any other meeting.

Built on Sarvam AI: **Saaras v4** (speech to text with speaker separation), **Sarvam-105B** (minutes and answers), **Bulbul** (spoken summaries and answers).

---

## Run it

Put your key in `.env` first (copy `.env.example` to `.env` and paste the key from dashboard.sarvam.ai).

**Windows:** double-click `run.bat`. It installs the packages, starts the server and opens http://localhost:8000. Keep the black window open while you use the app.

**Mac / Linux or a terminal:**

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Do not open `static/index.html` directly. The page only works through the server at http://localhost:8000.

ffmpeg is optional but recommended (Windows: `winget install Gyan.FFmpeg`). With it, uploads are converted to small MP3s and any video format works. Without it, the original file is sent to Saaras as it is.

Open http://localhost:8000. A sample Gram Sabha (Rampur Gram Panchayat, 2 October) loads on first run, so the UI works even before you add a key.

**Give the sample meeting real audio** (needs the key, takes about a minute):

```bash
python -m scripts.make_demo_audio        # voices all 30 turns with 5 different Bulbul voices
python -m scripts.reset_demo             # reloads the sample with the audio
```

The generated `app/demo/gram_sabha.mp3` (or `.wav` without ffmpeg) is also the best first test of the real pipeline. Upload it as a new meeting and check that Saaras finds 5 speakers.

---

## Priorities

### P0: must work for the demo (built)
| Feature | Where |
|---|---|
| Upload any audio or video, or record in the browser | home page |
| Speaker-separated transcript with timestamps (Saaras v4 batch, diarization, key terms) | `pipeline.process_meeting` |
| Minutes by meeting type: decisions, who does what by when, money mentioned, complaints, open issues, agenda. Every item cites the transcript lines it came from | `prompts.minutes_messages` |
| Click any violet stamp to play that exact moment | `app.js seekTo` |
| "Missed the meeting?" panel: one-minute spoken summary in 11 languages | `pipeline.brief` |
| Ask by text or voice in any language, answer comes back in your language with stamps, and can be read aloud | `pipeline.ask` |
| Long meetings (2 hours+): chunked minutes and minutes-as-index retrieval for Q&A | `make_minutes`, `_context_for_meeting` |

### P1: makes it stand out (built)
| Feature | Why it matters |
|---|---|
| Ask across all meetings of the same body ("was the road promised in September ever built?") | Accountability across meetings, not just one |
| Share page + QR code for the notice board, WhatsApp share | How absentees actually reach it |
| Rename speakers, rewrite minutes with real names | Model guesses names from what was said, people correct them |
| Download minutes as Markdown with timestamps | Panchayat secretary can paste into the register |
| SHA-256 fingerprint of the audio on the minutes | Anyone can check the recording was not edited later |
| Separate templates for court (with "not a certified record" note) and parliament (neutral positions, assurances given) | Same engine, different outputs |

### P2: next, if there is time
1. **Call-in line for absentees** using Sarvam Voice Agents: a missed call gets a call back with the summary, then the person asks questions by phone. Biggest impact for villages without smartphones.
2. **Live mode**: Saaras streaming over WebSocket so minutes build while the meeting runs.
3. **Promise tracker**: pull every "will be done in 15 days" into a list, and check it against the next meeting automatically.
4. **WhatsApp bot**: send the summary audio and answer questions on WhatsApp.
5. Login and roles (secretary uploads, public reads).

---

## How Sarvam is used

| Step | Model | Notes |
|---|---|---|
| Meeting audio to transcript | `saaras:v4` batch job, `with_diarization=True`, `keyterms` | Up to 2 hours per file. Spoken language can be set or auto-detected. |
| Spoken question to text | `saaras:v4` REST, `language_code="unknown"` | Detects the language, so the answer comes back in the same one |
| Minutes, answers, spoken script | `sarvam-105b`, JSON mode | 128K context. Full transcript fits for most meetings |
| Summary and answers read aloud | `bulbul:v3` (set `TTS_MODEL=bulbul:v4-flash` to try the newer one, its speaker names differ) | Split into chunks under the 2500-character limit and joined |

All Sarvam calls are in `app/sarvam_service.py`. Change models in `.env`.

---

## 3-minute demo script

1. **The problem (20 s).** India has 2.5 lakh Gram Panchayats. Most people cannot attend the Gram Sabha, and the written minutes, if any, are a few lines in a register. The same is true of Assembly debates and court hearings: public in theory, unreadable in practice.
2. **Upload (20 s).** Drop the Gram Sabha recording. Show the steps: Saaras separating 5 speakers, 105B writing minutes.
3. **Minutes (40 s).** Scroll the decisions. Click a violet stamp and the Sarpanch's actual voice plays at that line. "Nothing here is unsourced."
4. **The absentee (60 s).** Switch to the phone view from the QR code. Pick Tamil and tap "one-minute summary". Then hold the mic and ask in Hinglish: "Mera MGNREGA payment kab aayega?" The answer is in Hinglish, with stamps to the secretary's reply.
5. **Across meetings (20 s).** Ask "Was the school road approved?" across all meetings of the Panchayat.
6. **Scale (20 s).** Same engine, switch the template: a Lok Sabha debate clip, a court hearing. Close with the P2 call-in line.

**Getting real recordings for the demo:** Lok Sabha and Rajya Sabha proceedings are public on Sansad TV and the Digital Sansad site. The Supreme Court and several High Courts live-stream hearings. Check each court's rules before recording or reusing hearing audio. For Gram Sabha, use the Bulbul-voiced sample or record a mock meeting with your team.

---

## Project layout

```
app/
  main.py             API routes
  pipeline.py         audio -> transcript -> minutes, ask, spoken summary
  prompts.py          meeting-type templates and all prompts
  sarvam_service.py   every Sarvam API call
  store.py            JSON files under data/meetings/<id>/
  demo.py, demo/      sample Gram Sabha
static/               the web app (plain HTML, CSS, JS, no build step)
scripts/              make_demo_audio.py, reset_demo.py
tests/                pytest suite with a fake Sarvam service
```

## Tests

```bash
python -m pytest -q          # runs the whole flow with a fake Sarvam service, no key needed
python -m tests.fake_server  # the UI on port 8078 against the fake service
```

## Check first with a real key

The code follows the current Sarvam SDK (`sarvamai` 0.1.39) and docs, and the full flow is tested against a fake service. It has not yet run against the live API. On your first run, check:

- The batch job finishes and `diarized_transcript.entries` comes back (look at `data/meetings/<id>/transcript.json`).
- `sarvam-105b` returns clean JSON for the minutes. If it runs out of tokens on a long meeting, raise `max_tokens` in `pipeline.make_minutes`.
- If `reasoning_effort="low"` is rejected, set `reasoning_effort=None` in `sarvam_service.chat`.
