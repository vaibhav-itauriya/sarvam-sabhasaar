# SabhaSaar (सभासार)

Minutes for any public meeting, in any Indian language, where every point is stamped with the moment it was said. People who missed the meeting can listen to a one-minute summary in their own language or ask questions by voice.

Works for Gram Sabha and Panchayat meetings, Lok Sabha, Rajya Sabha and Vidhan Sabha proceedings, court and tribunal hearings, ward committees and any other meeting.

Built on Sarvam AI: **Saaras v4** (speech to text with speaker separation), **Sarvam-105B** (minutes and answers), **Bulbul** (spoken summaries and answers).

**Live demo:** https://sarvam-sabhasaar.onrender.com (free hosting, so the first visit after a quiet spell takes about a minute to wake up)

## Four ways to add a meeting

| Way | How it works | Good for |
|---|---|---|
| **Join an online meeting** | Open the meeting in another browser tab, pick that tab when SabhaSaar asks, tick "Also share tab audio". Optionally add your microphone. | Google Meet, Zoom, Teams, YouTube live of Sansad TV, court live streams |
| **Record in this room** | The phone or laptop microphone records the meeting around you | A Gram Sabha under the peepal tree, a ward meeting |
| **Record from a link** | Paste a stream or video link. The server records it, so you can close the page | Sansad TV or YouTube live, `.m3u8` streams |
| **Upload a recording** | Any audio or video file | Meetings that already happened |

While a meeting is being recorded, live captions appear about every 25 seconds and anyone who joined late can already ask "what has happened so far?". When you press **Stop and make minutes**, Saaras separates the speakers on the full recording and Sarvam-105B writes the minutes.

SabhaSaar does not sign in to Google Meet or Zoom as a bot. It records the sound of a tab you already have open, so it works with any meeting app without accounts or passwords. Tell everyone in the meeting that it is being recorded.

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
| Upload any audio or video | home page |
| Record live: an online meeting tab, the room microphone, or a stream link, with live captions and questions during the meeting | `app/live.py`, `Rec` in `app.js` |
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
2. **Live minutes**: rolling decisions and action items while the meeting is still running (captions and questions already work live).
3. **Promise tracker**: pull every "will be done in 15 days" into a list, and check it against the next meeting automatically.
4. **WhatsApp bot**: send the summary audio and answer questions on WhatsApp.
5. Login and roles (secretary uploads, public reads).

---

## How Sarvam is used

| Step | Model | Notes |
|---|---|---|
| Meeting audio to transcript | `saaras:v4` batch job, `with_diarization=True`, `keyterms` | Up to 2 hours per file. Spoken language can be set or auto-detected. |
| Spoken question to text | `saaras:v4` REST, `language_code="unknown"` | Detects the language, so the answer comes back in the same one |
| Live captions while recording | `saaras:v4` REST on each 25 s chunk | Silent chunks are skipped to save credits |
| Minutes, answers, spoken script | `sarvam-105b`, JSON mode | 128K context. Full transcript fits for most meetings |
| Summary and answers read aloud | `bulbul:v3` (set `TTS_MODEL=bulbul:v4-flash` to try the newer one, its speaker names differ) | Split into chunks under the 2500-character limit and joined |

All Sarvam calls are in `app/sarvam_service.py`. Change models in `.env`.

---

## 3-minute demo script

1. **The problem (20 s).** India has 2.5 lakh Gram Panchayats. Most people cannot attend the Gram Sabha, and the written minutes, if any, are a few lines in a register. The same is true of Assembly debates and court hearings: public in theory, unreadable in practice.
2. **Join live (30 s).** Pick "Join an online meeting", choose a Sansad TV live tab, and show captions arriving. Ask "what has happened so far?" in Hindi. Then press Stop and show Saaras separating speakers and 105B writing the minutes.
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
  live.py             live recording: chunks, live captions, stream links, stop -> minutes
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

## Notes from testing on the live deployment

- Sarvam-105B reasons before it answers, and the reasoning counts against `max_tokens`. Spoken summaries and translations run with reasoning switched off (`reasoning_effort=None`), and empty replies are retried automatically.
- Answers are checked for the right script (Tamil, Devanagari and so on). A reply in the wrong language is translated before it is shown or spoken.
- On Render's free plan, files (meetings) are wiped when the service restarts. Upload or record your demo meeting on the day you present.
- YouTube sometimes blocks recordings from cloud servers. If a YouTube link fails on "Record from a link", use "Join an online meeting" with the video open in a tab instead.
