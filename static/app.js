/* SabhaSaar frontend. Plain JS, no build step. */
const S = {
  config: null,
  data: null,          // current meeting payload
  audio: null,         // <audio> for the meeting
  poll: null,
  tab: "minutes",
  lang: safeGet("ss_lang") || "auto",
};
const SPEAKER_COLORS = ["#1D4E89", "#8A3B12", "#2E6B3A", "#7A2E6B", "#6B5E12", "#24626B", "#9A2F4F", "#3D3D8F"];
const STEPS = [
  ["preparing", "Preparing the audio"],
  ["transcribing", "Transcribing and separating speakers with Saaras"],
  ["summarising", "Writing the minutes with Sarvam-105B"],
  ["ready", "Ready to read, listen and ask"],
];
const SUGGEST = {
  gram_sabha: ["What was decided?", "क्या क्या प्रस्ताव पास हुए?", "Who has to do what, and by when?", "पैसे किस काम पर खर्च होंगे?"],
  parliament: ["What was the debate about?", "What did the minister reply?", "सरकार ने क्या आश्वासन दिया?", "Was anything passed?"],
  court: ["What did the judge order?", "अगली तारीख कब है?", "What did each side argue?", "What has to be filed before next hearing?"],
  municipal: ["What was decided?", "Which complaints were raised?", "Who is responsible for each task?"],
  general: ["What was decided?", "Summarise this in 3 lines", "What is still pending?"],
  live: ["What has happened so far?", "अब तक क्या हुआ?", "Has anything been decided yet?"],
};

/* ---------------- helpers ---------------- */
function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } }

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only used with trusted, static strings
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren() would print "null" for empty slots, so drop them first
function setKids(el, ...kids) { el.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false)); }

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), err ? 6000 : 2800);
}

function hms(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  return (hh ? `${hh}:` : "") + `${String(mm).padStart(hh ? 2 : 1, "0")}:${String(ss).padStart(2, "0")}`;
}
function fmtDate(iso) {
  if (!iso) return { day: "–", rest: "no date" };
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d)) return { day: "–", rest: iso };
  return { day: d.getDate(), rest: d.toLocaleString("en-IN", { month: "short", year: "numeric" }) };
}
function speakerColor(sid) {
  const ids = Object.keys(S.data?.meeting?.speakers || {}).sort((a, b) => Number(a) - Number(b));
  const i = Math.max(0, ids.indexOf(String(sid)));
  return SPEAKER_COLORS[i % SPEAKER_COLORS.length];
}
function speakerName(sid) {
  return S.data?.meeting?.speakers?.[sid]?.name || `Speaker ${Number(sid) + 1}`;
}
function typeInfo(t) { return S.config.meeting_types[t] || S.config.meeting_types.general; }
const PLAY_ICON = '<svg viewBox="0 0 10 10"><path d="M1 0l9 5-9 5z"/></svg>';
const PAUSE_ICON = '<svg viewBox="0 0 10 10"><path d="M1 0h3v10H1zM6 0h3v10H6z"/></svg>';
const MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

/* ---------------- routing ---------------- */
window.addEventListener("hashchange", route);
window.addEventListener("load", async () => {
  try {
    S.config = await api("/api/config");
  } catch (e) {
    $("#view").replaceChildren(h("section", { class: "home-head" },
      h("h1", {}, "The SabhaSaar server is not running"),
      h("p", {}, "This page needs the Python server behind it. Double-click run.bat in the sabhasaar folder (or run: python -m uvicorn app.main:app --port 8000), then open http://localhost:8000 in your browser.")));
    return;
  }
  const ks = $("#keyState");
  ks.replaceChildren(S.config.has_key ? "Sarvam AI" : "Sample mode.", h("span", {}, S.config.has_key ? "connected" : "add SARVAM_API_KEY to process recordings"));
  ks.classList.toggle("on", S.config.has_key);
  route();
});

function route() {
  clearInterval(S.poll);
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const params = new URLSearchParams(query || "");
  const m = path.match(/^\/m\/([a-z0-9]+)(\/listen)?$/i);
  if (m) return meetingPage(m[1], m[2] ? "listen" : "full", params.get("seg"));
  teardownPlayer();
  home();
}

/* ---------------- home ---------------- */
async function home() {
  const view = $("#view");
  view.replaceChildren();
  const meetings = await api("/api/meetings");
  view.append(
    h("section", { class: "home-head" },
      h("h1", {}, "Every meeting, on the record, in every language."),
      h("p", {}, "Upload a Gram Sabha, an Assembly debate or a court hearing. SabhaSaar writes minutes where every point is stamped with the moment it was said, and anyone who missed it can hear a one-minute summary or ask questions in their own language.")),
    h("div", { class: "home-grid" }, registerList(meetings), addForm(meetings)),
  );
  document.title = "SabhaSaar";
}

function statusBadge(m) {
  if (m.status === "live") return h("span", { class: "badge live" }, "Live");
  if (m.status === "ready") return h("span", { class: "badge ready" }, "Ready");
  if (m.status === "failed") return h("span", { class: "badge failed" }, "Failed");
  return h("span", { class: "badge working" }, "Processing");
}

function registerList(meetings) {
  const rows = meetings.map((m) => {
    const d = fmtDate(m.date);
    return h("a", { class: "reg-row", href: `#/m/${m.id}` },
      h("div", { class: "reg-date" }, h("b", {}, d.day), h("small", {}, d.rest)),
      h("div", {},
        h("div", { class: "reg-title" }, m.title || "Untitled meeting"),
        h("div", { class: "reg-sub" }, [m.body, typeInfo(m.meeting_type).label].filter(Boolean).join(", "))),
      statusBadge(m));
  });
  return h("section", { class: "register", "aria-label": "Meetings" },
    h("header", {}, h("h2", {}, "Meeting register"), h("span", {}, `${meetings.length} recorded`)),
    rows.length ? rows : h("p", { class: "reg-empty" }, "No meetings yet. Add your first recording on the right."));
}

const MODES = {
  upload: { label: "Upload a recording", hint: "An audio or video file of a meeting that already happened.", cta: "Make minutes" },
  tab: { label: "Join an online meeting", hint: "Google Meet, Zoom, Teams, a YouTube live of Sansad TV or a court live stream. Open it in another tab, then pick that tab and tick “Also share tab audio”.", cta: "Choose the meeting tab and start" },
  mic: { label: "Record in this room", hint: "For a Gram Sabha or meeting happening around you. Keep this device near the speakers and the screen on.", cta: "Start recording" },
  link: { label: "Record from a link", hint: "Paste a live stream or video link. The server records it, so you can close this page.", cta: "Start recording the link" },
};
const canShareTab = () => !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) && !/Android|iPhone|iPad/i.test(navigator.userAgent);

function addForm(meetings) {
  let file = null;
  let mode = canShareTab() ? "tab" : "mic";
  const fileInput = h("input", { type: "file", accept: "audio/*,video/*", class: "visually-hidden", id: "fileIn" });
  const dropText = h("span", {}, "Drop an audio or video file here, or click to choose one");
  const drop = h("label", { class: "drop", for: "fileIn" }, dropText);
  const setFile = (f) => {
    file = f;
    drop.classList.toggle("has-file", !!f);
    dropText.textContent = f ? `${f.name} (${(f.size / 1e6).toFixed(1)} MB)` : "Drop an audio or video file here, or click to choose one";
  };
  fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drag"); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });

  const urlInput = h("input", { id: "f-url", type: "url", placeholder: "https://www.youtube.com/watch?v=... or a .m3u8 stream link" });
  const withMic = h("input", { type: "checkbox", id: "f-withmic" });
  const modeBody = h("div", { class: "mode-body" });
  const submit = h("button", { type: "submit", class: "btn green wide", disabled: !S.config.has_key }, MODES[mode].cta);

  const modeRadios = h("div", { class: "modes", role: "radiogroup", "aria-label": "How to add the meeting" },
    Object.entries(MODES).map(([k, v]) => {
      const disabled = k === "tab" && !canShareTab();
      return h("label", { class: "mode" + (disabled ? " off" : ""), title: disabled ? "Needs Chrome or Edge on a computer" : "" },
        h("input", { type: "radio", name: "mode", value: k, checked: k === mode, disabled }),
        h("span", {}, v.label));
    }));
  const renderMode = () => {
    setKids(modeBody,
      h("p", { class: "hint" }, MODES[mode].hint),
      mode === "upload" ? h("div", {}, fileInput, drop) : null,
      mode === "tab" ? h("label", { class: "check" }, withMic, " Also record my microphone (tick this if you will speak in the meeting)") : null,
      mode === "link" ? h("div", { class: "field" }, h("label", { for: "f-url" }, "Stream or video link"), urlInput) : null,
      mode !== "upload" ? h("p", { class: "hint consent" }, "Tell everyone in the meeting that it is being recorded.") : null,
    );
    submit.textContent = MODES[mode].cta;
  };
  modeRadios.addEventListener("change", (e) => { mode = e.target.value; renderMode(); });
  renderMode();

  const langOpts = (withAuto) => [
    withAuto ? h("option", { value: "unknown" }, "Detect automatically") : null,
    ...Object.entries(S.config.languages).map(([k, v]) => h("option", { value: k }, v)),
  ];
  const bodies = [...new Set(meetings.map((m) => m.body).filter(Boolean))];
  const today = new Date().toISOString().slice(0, 10);

  const form = h("form", { class: "panel" },
    h("h2", {}, "Add a meeting"),
    S.config.has_key ? null : h("p", { class: "hint" }, "Add SARVAM_API_KEY to the .env file and restart to record or process meetings. The sample meeting works without it."),
    modeRadios, modeBody,
    h("div", { class: "field" }, h("label", { for: "f-type" }, "What kind of meeting"),
      h("select", { id: "f-type", name: "meeting_type" },
        Object.entries(S.config.meeting_types).map(([k, v]) => h("option", { value: k }, v.label)))),
    h("div", { class: "field" }, h("label", { for: "f-title" }, "Title"),
      h("input", { id: "f-title", name: "title", placeholder: "Leave empty to let SabhaSaar name it" })),
    h("div", { class: "field" }, h("label", { for: "f-body" }, "Which body"),
      h("input", { id: "f-body", name: "body", list: "bodies", placeholder: "Rampur Gram Panchayat, Lok Sabha, District Court Kanpur" }),
      h("datalist", { id: "bodies" }, bodies.map((b) => h("option", { value: b }))),
      h("small", {}, "Meetings of the same body can be asked about together.")),
    h("div", { class: "row2" },
      h("div", { class: "field" }, h("label", { for: "f-date" }, "Date"), h("input", { id: "f-date", type: "date", name: "date", value: today })),
      h("div", { class: "field" }, h("label", { for: "f-lang" }, "Spoken language"), h("select", { id: "f-lang", name: "language_code" }, langOpts(true)))),
    h("details", { class: "more" }, h("summary", {}, "More options"),
      h("div", { class: "row2" },
        h("div", { class: "field" }, h("label", { for: "f-spk" }, "Number of speakers"), h("input", { id: "f-spk", name: "num_speakers", type: "number", min: 1, max: 30, placeholder: "Not sure" })),
        h("div", { class: "field" }, h("label", { for: "f-ml" }, "Write minutes in"), h("select", { id: "f-ml", name: "minutes_language" }, langOpts(false)))),
      h("div", { class: "field" }, h("label", { for: "f-kt" }, "Names and terms to listen for"),
        h("input", { id: "f-kt", name: "keyterms", placeholder: "MGNREGA, Jal Jeevan Mission, Sunita Verma" }),
        h("small", {}, "Comma separated. Helps Saaras spell local names and schemes correctly."))),
    submit,
  );
  $("#f-ml", form).value = "en-IN";

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (Rec.active) return toast("A recording is already running. Stop it first.", true);
    const fd = new FormData(form);
    fd.delete("mode");
    const busy = (txt) => { submit.disabled = true; submit.textContent = txt; };
    const reset = () => { submit.disabled = !S.config.has_key; submit.textContent = MODES[mode].cta; };
    try {
      if (mode === "upload") {
        if (!file) return toast("Choose a recording first", true);
        fd.append("file", file, file.name);
        busy("Uploading…");
        const m = await api("/api/meetings", { method: "POST", body: fd });
        location.hash = `#/m/${m.id}`;
        return;
      }
      if (mode === "link") {
        if (!urlInput.value.trim()) return toast("Paste the stream link first", true);
        fd.append("source", "link");
        fd.append("url", urlInput.value.trim());
        busy("Connecting…");
        const m = await api("/api/live", { method: "POST", body: fd });
        location.hash = `#/m/${m.id}`;
        return;
      }
      // tab or mic: ask for the sound first, while the click still counts as a user action
      const streams = await captureStreams(mode, withMic.checked);
      fd.append("source", mode);
      busy("Starting…");
      let m;
      try { m = await api("/api/live", { method: "POST", body: fd }); }
      catch (err) { streams.forEach((s) => s.getTracks().forEach((t) => t.stop())); throw err; }
      Rec.start(m, streams);
      location.hash = `#/m/${m.id}`;
    } catch (err) {
      toast(err.message || String(err), true);
      reset();
    }
  });
  return form;
}

/* ---------------- live recording in the browser ---------------- */
async function captureStreams(mode, withMic) {
  const streams = [];
  if (mode === "tab") {
    const ds = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      selfBrowserSurface: "exclude", systemAudio: "include", surfaceSwitching: "include",
    });
    if (!ds.getAudioTracks().length) {
      ds.getTracks().forEach((t) => t.stop());
      throw new Error("No sound came with that share. Pick the meeting tab (not a window) and tick “Also share tab audio”.");
    }
    streams.push(ds);
  }
  if (mode === "mic" || withMic) {
    try {
      streams.push(await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }));
    } catch (e) {
      streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
      throw new Error("Microphone not available: " + e.message);
    }
  }
  return streams;
}

function encodeWav(int16, rate = 16000) {
  const buf = new ArrayBuffer(44 + int16.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + int16.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, int16.length * 2, true);
  new Int16Array(buf, 44).set(int16);
  return new Blob([buf], { type: "audio/wav" });
}

function downsample(float32, fromRate, toRate = 16000) {
  const ratio = fromRate / toRate;
  const n = Math.floor(float32.length / ratio);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(float32.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += float32[j];
    const s = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

const Rec = {
  active: null,
  CHUNK_SECONDS: 25,

  start(meeting, streams) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const mix = ctx.createGain();
    streams.forEach((s) => ctx.createMediaStreamSource(new MediaStream(s.getAudioTracks())).connect(mix));
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const silent = ctx.createGain();
    silent.gain.value = 0;
    mix.connect(proc); proc.connect(silent); silent.connect(ctx.destination);
    const r = { meeting, streams, ctx, proc, parts: [], samples: 0, seq: 0, level: 0, startedAt: Date.now(),
                queue: Promise.resolve(), stopping: false, wakeLock: null };
    proc.onaudioprocess = (e) => {
      const d = e.inputBuffer.getChannelData(0);
      r.parts.push(new Float32Array(d));
      r.samples += d.length;
      let sum = 0;
      for (let i = 0; i < d.length; i += 8) sum += d[i] * d[i];
      r.level = Math.min(1, Math.sqrt(sum / (d.length / 8)) * 4);
      if (r.samples >= Rec.CHUNK_SECONDS * ctx.sampleRate) Rec.flush();
    };
    // the "Stop sharing" button in the browser ends the recording too
    streams.forEach((s) => s.getVideoTracks().forEach((t) => t.addEventListener("ended", () => Rec.stop())));
    if (navigator.wakeLock) navigator.wakeLock.request("screen").then((w) => (r.wakeLock = w)).catch(() => {});
    Rec.active = r;
    window.addEventListener("beforeunload", Rec.warn);
    Rec.renderBar();
    r.barTimer = setInterval(Rec.renderBar, 500);
  },

  warn(e) { if (Rec.active) { e.preventDefault(); e.returnValue = ""; } },

  flush(final = false) {
    const r = Rec.active;
    if (!r || !r.samples) return r ? r.queue : Promise.resolve();
    const all = new Float32Array(r.samples);
    let o = 0;
    for (const p of r.parts) { all.set(p, o); o += p.length; }
    r.parts = []; r.samples = 0;
    if (final && all.length < r.ctx.sampleRate) return r.queue;  // under a second: drop
    const wav = encodeWav(downsample(all, r.ctx.sampleRate));
    const seq = r.seq++;
    const mid = r.meeting.id;
    r.queue = r.queue.then(() => Rec.upload(mid, seq, wav));
    return r.queue;
  },

  async upload(mid, seq, wav, tries = 3) {
    const fd = new FormData();
    fd.append("audio", wav, `chunk-${seq}.wav`);
    fd.append("seq", seq);
    try {
      const res = await fetch(`/api/live/${mid}/chunk`, { method: "POST", body: fd });
      if (res.status === 409) { toast("This meeting was stopped. Recording ended."); Rec.cleanup(); return; }
      if (!res.ok) throw new Error(res.status);
    } catch (e) {
      if (tries > 1) { await new Promise((ok) => setTimeout(ok, 2000)); return Rec.upload(mid, seq, wav, tries - 1); }
      toast("A part of the recording could not be sent. Check your internet.", true);
    }
  },

  async stop() {
    const r = Rec.active;
    if (!r || r.stopping) return;
    r.stopping = true;
    Rec.renderBar();
    r.proc.onaudioprocess = null;
    await Rec.flush(true);
    const mid = r.meeting.id;
    Rec.cleanup();
    try { await api(`/api/live/${mid}/stop`, { method: "POST" }); } catch (e) { toast(e.message, true); }
    if (location.hash.startsWith(`#/m/${mid}`)) route(); else location.hash = `#/m/${mid}`;
  },

  cleanup() {
    const r = Rec.active;
    if (!r) return;
    clearInterval(r.barTimer);
    try { r.proc.disconnect(); r.ctx.close(); } catch { /* already closed */ }
    r.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
    if (r.wakeLock) r.wakeLock.release().catch(() => {});
    Rec.active = null;
    window.removeEventListener("beforeunload", Rec.warn);
    $("#recbar")?.remove();
    document.body.classList.remove("recording");
  },

  renderBar() {
    const r = Rec.active;
    if (!r) return;
    let bar = $("#recbar");
    if (!bar) {
      bar = h("div", { id: "recbar", class: "recbar", role: "status" });
      document.body.prepend(bar);
      document.body.classList.add("recording");
    }
    const secs = (Date.now() - r.startedAt) / 1000;
    setKids(bar,
      h("span", { class: "rec-dot", "aria-hidden": "true" }),
      h("a", { href: `#/m/${r.meeting.id}` }, r.stopping ? "Finishing the recording…" : `Recording ${r.meeting.title || "meeting"}`),
      h("span", { class: "rec-time" }, hms(secs)),
      h("span", { class: "rec-level", title: "Sound level" }, h("span", { style: { width: `${Math.round(r.level * 100)}%` } })),
      r.stopping ? null : h("button", { class: "btn small", onclick: () => Rec.stop() }, "Stop and make minutes"),
    );
  },
};

/* ---------------- meeting ---------------- */
async function meetingPage(id, mode, jumpSeg) {
  const view = $("#view");
  try {
    S.data = await api(`/api/meetings/${id}`);
  } catch (e) {
    view.replaceChildren(h("div", { class: "home-head" }, h("h1", {}, "Meeting not found"), h("a", { href: "#/" }, "Back to all meetings")));
    return;
  }
  const { meeting } = S.data;
  document.title = `${meeting.title || "Meeting"} | SabhaSaar`;
  if (meeting.status === "live") {
    teardownPlayer();
    view.replaceChildren(liveHead(meeting), h("div", { class: "m-grid" }, liveCaptionsView(), missedPanel()));
    renderCaptions(S.data.captions || []);
    S.poll = setInterval(async () => {
      const lc = await api(`/api/live/${id}/captions`).catch(() => null);
      if (!lc) return;
      if (lc.status !== "live") { clearInterval(S.poll); meetingPage(id, mode); return; }
      renderCaptions(lc.captions);
      const so = $("#soFar");
      if (so) so.textContent = soFar(lc.duration);
    }, 4000);
    return;
  }
  if (meeting.status !== "ready") {
    teardownPlayer();
    view.replaceChildren(meetingHead(meeting, false), progressView(meeting));
    S.poll = setInterval(async () => {
      const st = await api(`/api/meetings/${id}/status`).catch(() => null);
      if (st && st.status !== meeting.status) { clearInterval(S.poll); meetingPage(id, mode); }
      else if (st) { const note = $("#statusNote"); if (note) note.textContent = st.status_note; }
    }, 3000);
    return;
  }
  setupPlayer();
  if (mode === "listen") {
    const mt = typeInfo(meeting.meeting_type);
    view.replaceChildren(h("div", { class: "listen" },
      h("a", { class: "back", href: `#/m/${id}` }, "Read the full minutes"),
      h("h1", {}, meeting.title),
      h("p", { class: "m-meta" }, [meeting.body, fmtDate(meeting.date).day + " " + fmtDate(meeting.date).rest, mt.label].filter(Boolean).join(", ")),
      h("p", { class: "lead" }, S.data.minutes?.one_line || ""),
      missedPanel()));
  } else {
    view.replaceChildren(
      meetingHead(meeting, true),
      h("div", { class: "m-grid" },
        h("div", {}, h("p", { class: "lead" }, S.data.minutes?.one_line || ""), tabsView()),
        missedPanel()));
  }
  if (jumpSeg !== null && jumpSeg !== undefined) setTimeout(() => seekTo(Number(jumpSeg)), 300);
}

function meetingHead(m, ready) {
  const mt = typeInfo(m.meeting_type);
  const d = fmtDate(m.date);
  const nSpk = Object.keys(m.speakers || {}).length;
  const actions = ready ? h("div", { class: "m-actions" },
    h("button", { class: "btn", onclick: () => shareDialog(m) }, "Share with people who missed it"),
    h("a", { class: "btn ghost", href: `/api/meetings/${m.id}/export.md`, download: `${(m.title || "minutes").replace(/\W+/g, "-")}.md` }, "Download minutes"),
    S.config.has_key ? h("button", { class: "btn ghost", onclick: () => rebuild(m.id) }, "Rewrite minutes") : null,
  ) : null;
  return h("header", { class: "m-head" },
    h("a", { class: "back", href: "#/" }, "All meetings"),
    h("h1", {}, m.title || "New meeting"),
    h("div", { class: "m-meta" },
      m.body ? h("span", {}, h("strong", {}, m.body)) : null,
      h("span", {}, `${d.day} ${d.rest}`),
      h("span", {}, mt.label),
      m.duration ? h("span", {}, m.duration < 60 ? `${Math.round(m.duration)} sec` : `${Math.round(m.duration / 60)} min`) : null,
      nSpk ? h("span", {}, `${nSpk} speakers`) : null,
      m.detected_language ? h("span", {}, `Spoken in ${S.config.languages[m.detected_language] || m.detected_language}`) : null),
    actions);
}

const LIVE_LABELS = { tab: "Online meeting", mic: "Recorded in the room", link: "Stream link" };

function soFar(sec) {
  if (!sec) return "Waiting for the first half minute";
  return sec < 60 ? `${Math.round(sec)} sec recorded so far` : `${Math.round(sec / 60)} min recorded so far`;
}

function liveHead(m) {
  const d = fmtDate(m.date);
  const mine = Rec.active && Rec.active.meeting.id === m.id;
  const stopBtn = h("button", { class: "btn rec on" }, "Stop and make minutes");
  stopBtn.addEventListener("click", async () => {
    if (mine) return Rec.stop();
    stopBtn.disabled = true;
    try { await api(`/api/live/${m.id}/stop`, { method: "POST" }); route(); }
    catch (e) { toast(e.message, true); stopBtn.disabled = false; }
  });
  const note = m.live_source === "link" ? "The server is recording this stream. You can close this page."
    : mine ? "This browser is recording. Keep this tab open until the meeting ends."
    : "Another device is recording this meeting. Stopping here ends the recording for everyone.";
  return h("header", { class: "m-head" },
    h("a", { class: "back", href: "#/" }, "All meetings"),
    h("h1", {}, m.title || "Live meeting"),
    h("div", { class: "m-meta" },
      h("span", { class: "badge live" }, "Live now"),
      m.body ? h("span", {}, h("strong", {}, m.body)) : null,
      h("span", {}, `${d.day} ${d.rest}`),
      h("span", {}, LIVE_LABELS[m.live_source] || "Live"),
      h("span", { id: "soFar" }, soFar(m.duration))),
    h("p", { class: "hint live-note" }, note),
    h("div", { class: "m-actions" }, stopBtn,
      h("button", { class: "btn ghost", onclick: () => shareDialog(m) }, "Share so others can follow")));
}

function liveCaptionsView() {
  return h("section", { class: "live-caps" },
    h("h2", {}, "Live captions"),
    h("p", { class: "hint" }, "A new line appears about every half minute. Speaker names and the full minutes come when the meeting ends."),
    h("div", { id: "capList", class: "transcript", "aria-live": "polite" }));
}

function renderCaptions(caps) {
  const list = $("#capList");
  if (!list) return;
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  let n = 0;
  const segs = [];
  const rows = caps.map((c) => {
    let text, cls = "seg", id = null;
    if (c.status === "done" && c.text) { text = c.text; id = `seg-${n}`; segs.push({ id: n, start: c.start, end: c.end, speaker: "0", text: c.text }); n++; }
    else if (c.status === "pending") { text = "Listening…"; cls += " pending"; }
    else if (c.status === "silent" || (c.status === "done" && !c.text)) { text = "(quiet)"; cls += " pending"; }
    else if (c.status === "skipped") { text = "(will be in the final transcript)"; cls += " pending"; }
    else { text = "(could not caption this part, it will still be in the minutes)"; cls += " pending"; }
    return h("div", { class: cls, id }, h("div", { class: "seg-who" }, h("span", {}, hms(c.start))), h("div", { class: "seg-text" }, text));
  });
  list.replaceChildren(...(rows.length ? rows : [h("p", { class: "reg-empty" }, "Waiting for the first half minute of sound…")]));
  if (S.data) S.data.transcript.segments = segs;
  if (nearBottom) list.scrollTop = list.scrollHeight;
}

function progressView(m) {
  const idx = Math.max(0, STEPS.findIndex(([k]) => k === m.status));
  return h("section", { class: "progress" },
    h("h2", { style: { fontFamily: "var(--serif)", fontWeight: 400, margin: 0 } }, m.status === "failed" ? "Something went wrong" : "Working on it"),
    h("ol", {}, STEPS.map(([k, label], i) =>
      h("li", { class: m.status === "failed" ? "" : i < idx ? "done" : i === idx ? "now" : "" }, label))),
    h("p", { id: "statusNote", class: m.status === "failed" ? "err" : "thinking" }, m.status_note || ""),
    m.status === "failed" && S.config.has_key ? h("button", { class: "btn", onclick: () => rebuild(m.id) }, "Try again") : null);
}

async function rebuild(id) {
  try { await api(`/api/meetings/${id}/rebuild`, { method: "POST" }); toast("Rewriting the minutes"); route(); }
  catch (e) { toast(e.message, true); }
}

/* ---------- tabs ---------- */
function tabsView() {
  const body = h("div", { id: "tabBody" });
  const mk = (key, label) => h("button", {
    role: "tab", "aria-selected": String(S.tab === key),
    onclick: () => { S.tab = key; const p = body.parentElement; p.replaceWith(tabsView()); },
  }, label);
  const segCount = S.data.transcript.segments.length;
  body.append(S.tab === "minutes" ? minutesView() : transcriptView());
  return h("div", {}, h("div", { class: "tabs", role: "tablist" }, mk("minutes", "Minutes"), mk("transcript", `Full transcript (${segCount} turns)`)), body);
}

function stamps(refs) {
  const segs = S.data.transcript.segments;
  const uniq = [...new Set((refs || []).filter((r) => r >= 0 && r < segs.length))].slice(0, 4);
  if (!uniq.length) return null;
  return h("span", { class: "stamps" }, uniq.map((r) =>
    h("button", { class: "stamp", title: `Play from ${hms(segs[r].start)}, ${speakerName(segs[r].speaker)}`, onclick: () => seekTo(r), html: PLAY_ICON + `<span>${hms(segs[r].start)}</span>` })));
}

function outcomeClass(o) {
  o = (o || "").toLowerCase();
  if (/defer|later|pending|postpon|next/.test(o)) return "deferred";
  if (/reject|fail|withdr|not pass/.test(o)) return "rejected";
  return "passed";
}

function minutesView() {
  const { minutes, meeting } = S.data;
  if (!minutes) return h("p", {}, "No minutes yet.");
  const mt = typeInfo(meeting.meeting_type);
  const sec = (title, n, ...content) => h("section", { class: "sec" }, h("h2", {}, title, n !== null ? h("span", { class: "count" }, n) : null), ...content);
  const out = [];
  if (mt.disclaimer) out.push(h("p", { class: "disclaimer" }, mt.disclaimer));

  out.push(sec("What happened", null, h("ul", { class: "points" },
    minutes.summary.map((p) => h("li", {}, p.text, stamps(p.refs))))));

  if (minutes.decisions.length) out.push(sec(mt.decisions, minutes.decisions.length, h("ul", { class: "points decisions" },
    minutes.decisions.map((d) => h("li", {}, d.text,
      h("div", { class: "dec-line" }, d.outcome ? h("span", { class: `badge ${outcomeClass(d.outcome)}` }, d.outcome) : null, stamps(d.refs)))))));

  if (minutes.actions.length) out.push(sec(mt.actions, minutes.actions.length, h("table", { class: "actions" },
    h("thead", {}, h("tr", {}, h("th", {}, "What"), h("th", {}, "Who"), h("th", {}, "By when"), h("th", {}, ""))),
    h("tbody", {}, minutes.actions.map((a) => h("tr", {},
      h("td", {}, a.task || a.text), h("td", { class: "who" }, a.owner || "Not said"),
      h("td", { class: "when" }, a.deadline || "Not said"), h("td", {}, stamps(a.refs))))))));

  for (const [key, label] of Object.entries(mt.extras || {})) {
    const items = minutes.extras?.[key] || [];
    if (items.length) out.push(sec(label, items.length, h("ul", { class: "points" }, items.map((p) => h("li", {}, p.text, stamps(p.refs))))));
  }
  if (minutes.open_issues.length) out.push(sec("Left unresolved", minutes.open_issues.length, h("ul", { class: "points open" },
    minutes.open_issues.map((p) => h("li", {}, p.text, stamps(p.refs))))));

  if (minutes.topics.length) out.push(sec("Agenda, in the order discussed", null, h("ol", { class: "agenda" },
    minutes.topics.map((t) => h("li", {}, h("div", {}, h("b", {}, t.title), stamps(t.start_ref !== null ? [t.start_ref] : []), h("p", {}, t.summary)))))));

  out.push(sec("Who spoke", Object.keys(meeting.speakers || {}).length, speakersView()));
  if (meeting.audio_sha256) out.push(h("p", { class: "fingerprint" }, `Audio fingerprint (SHA-256): ${meeting.audio_sha256}. Anyone can check the recording has not been changed since these minutes were made.`));
  return h("div", {}, out);
}

function speakersView() {
  const { meeting, minutes } = S.data;
  const info = Object.fromEntries((minutes.speakers || []).map((s) => [String(s.id), s]));
  const ids = Object.keys(meeting.speakers || {}).sort((a, b) => Number(a) - Number(b));
  const grid = h("div", { class: "speakers" });
  for (const sid of ids) {
    const sp = meeting.speakers[sid];
    const nameEl = h("span", {}, sp.name, sp.named_by === "model" ? h("span", { class: "guess" }, " (guessed)") : null);
    const btn = h("button", { type: "button" }, "Rename");
    btn.addEventListener("click", () => {
      const input = h("input", { value: sp.name, "aria-label": "Speaker name", style: { width: "100%", padding: "2px 6px" } });
      const save = async () => {
        try {
          S.data.meeting = await api(`/api/meetings/${meeting.id}/speakers`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ speakers: { [sid]: input.value } }) });
          toast("Name saved. Use Rewrite minutes to update names in the text.");
          grid.replaceWith(speakersView());
        } catch (e) { toast(e.message, true); }
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") grid.replaceWith(speakersView()); });
      nameEl.replaceWith(input);
      btn.textContent = "Save";
      btn.onclick = save;
      input.focus();
    }, { once: true });
    grid.append(h("div", { class: "spk", style: { "--c": speakerColor(sid) } },
      h("div", { class: "spk-name" }, nameEl, btn),
      sp.role ? h("div", { class: "spk-role" }, sp.role) : null,
      info[sid]?.key_points?.length ? h("ul", {}, info[sid].key_points.map((k) => h("li", {}, k))) : null));
  }
  return grid;
}

function transcriptView() {
  const segs = S.data.transcript.segments;
  return h("div", { class: "transcript", id: "transcript" }, segs.map((s) =>
    h("div", { class: "seg", id: `seg-${s.id}`, style: { "--c": speakerColor(s.speaker) }, onclick: () => seekTo(s.id, false) },
      h("div", { class: "seg-who" }, h("b", {}, speakerName(s.speaker)), h("span", {}, hms(s.start))),
      h("div", { class: "seg-text" }, s.text))));
}

/* ---------- jumping to a moment ---------- */
function seekTo(segId, switchTab = true) {
  const seg = S.data.transcript.segments[segId];
  if (!seg) return;
  if (S.audio) {
    S.audio.currentTime = seg.start;
    S.audio.play().catch(() => {});
  }
  if (!S.audio || switchTab) {
    if (!S.audio && S.tab !== "transcript" && $("#tabBody")) { S.tab = "transcript"; $("#tabBody").parentElement.replaceWith(tabsView()); }
  }
  const el = document.getElementById(`seg-${segId}`);
  if (el) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
  } else if (!S.audio) toast(`${speakerName(seg.speaker)} at ${hms(seg.start)}: ${seg.text.slice(0, 120)}`);
}

/* ---------------- player ---------------- */
function teardownPlayer() {
  if (S.audio) { S.audio.pause(); S.audio = null; }
  const p = $("#player");
  p.hidden = true;
  p.replaceChildren();
  document.body.style.setProperty("--player-h", "0px");
}

function setupPlayer() {
  teardownPlayer();
  const { meeting, transcript, minutes } = S.data;
  const p = $("#player");
  p.hidden = false;
  if (!meeting.audio_file) {
    p.append(h("div", { class: "no-audio" }, "This sample meeting has no recording. Click any time stamp to jump to that line in the transcript."));
    document.body.style.setProperty("--player-h", "64px");
    return;
  }
  const audio = new Audio(`/api/meetings/${meeting.id}/audio`);
  audio.preload = "metadata";
  S.audio = audio;
  const pp = h("button", { class: "pp", "aria-label": "Play", html: PLAY_ICON });
  const time = h("span", { class: "time" }, "0:00");
  const fill = h("div", { class: "fill" });
  const track = h("div", { class: "track", role: "slider", "aria-label": "Seek", tabindex: 0 }, h("div", { class: "bar" }), fill);
  const now = h("div", { class: "now-speaking" });
  const segs = transcript.segments;
  const dur = () => audio.duration || meeting.duration || (segs.at(-1)?.end ?? 1);

  const addMarks = () => {
    track.querySelectorAll(".mark").forEach((m) => m.remove());
    for (const t of minutes?.topics || []) {
      const s = segs[t.start_ref];
      if (!s) continue;
      track.append(h("div", { class: "mark", style: { left: `${(s.start / dur()) * 100}%` } }, h("span", {}, t.title)));
    }
  };
  addMarks();
  audio.addEventListener("loadedmetadata", addMarks);
  pp.addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => { pp.innerHTML = PAUSE_ICON; pp.setAttribute("aria-label", "Pause"); });
  audio.addEventListener("pause", () => { pp.innerHTML = PLAY_ICON; pp.setAttribute("aria-label", "Play"); });
  let lastActive = -1;
  audio.addEventListener("timeupdate", () => {
    const t = audio.currentTime;
    fill.style.width = `${(t / dur()) * 100}%`;
    time.textContent = `${hms(t)} / ${hms(dur())}`;
    let idx = -1;
    for (let i = 0; i < segs.length; i++) { if (segs[i].start <= t + 0.05) idx = i; else break; }
    if (idx !== lastActive) {
      document.getElementById(`seg-${lastActive}`)?.classList.remove("active");
      document.getElementById(`seg-${idx}`)?.classList.add("active");
      lastActive = idx;
      if (idx >= 0) {
        const s = segs[idx];
        now.style.setProperty("--c", speakerColor(s.speaker));
        now.replaceChildren(h("b", {}, speakerName(s.speaker)), " speaking");
      }
    }
  });
  const seekFromEvent = (e) => {
    const r = track.getBoundingClientRect();
    audio.currentTime = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * dur();
  };
  track.addEventListener("click", seekFromEvent);
  track.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") audio.currentTime += 10;
    if (e.key === "ArrowLeft") audio.currentTime -= 10;
    if (e.key === " ") { e.preventDefault(); pp.click(); }
  });
  p.append(pp, time, track, now);
  document.body.style.setProperty("--player-h", "70px");
}

/* ---------------- for people who missed it ---------------- */
function missedPanel() {
  const { meeting } = S.data;
  const langSel = h("select", { class: "lang-select", id: "langSel", "aria-label": "Your language" },
    h("option", { value: "auto" }, "Same as my question"),
    Object.entries(S.config.languages).map(([k, v]) => h("option", { value: k }, v)));
  langSel.value = S.lang;
  langSel.addEventListener("change", () => { S.lang = langSel.value; safeSet("ss_lang", S.lang); });
  const briefLang = () => (S.lang !== "auto" ? S.lang : (S.config.languages[meeting.detected_language] ? meeting.detected_language : "hi-IN"));

  const isLive = meeting.status === "live";
  const briefBox = h("div", { class: "brief-box" });
  const briefBtn = h("button", { class: "btn green wide" }, "Listen to a one-minute summary");
  briefBtn.addEventListener("click", async () => {
    briefBtn.disabled = true;
    briefBtn.textContent = "Preparing the summary…";
    try {
      const b = await postJSON(`/api/meetings/${meeting.id}/brief`, { language: briefLang() });
      setKids(briefBox,
        b.audio_url ? h("audio", { controls: true, autoplay: true, src: b.audio_url }) : null,
        h("details", { open: !b.audio_url }, h("summary", {}, "Read it instead"), h("p", {}, b.script)));
      briefBtn.textContent = "Listen in another language";
    } catch (e) { toast(e.message, true); briefBtn.textContent = "Listen to a one-minute summary"; }
    briefBtn.disabled = false;
  });
  briefBox.append(isLive ? h("p", { style: { margin: 0 } }, "The one-minute summary will be ready when the meeting ends. You can already ask what has happened so far.") : briefBtn);

  const textarea = h("textarea", { placeholder: "Ask anything. For example: was the road approved? सड़क का क्या हुआ?", "aria-label": "Your question" });
  const thread = h("div", { class: "thread", "aria-live": "polite" });
  const others = S.data.others_in_body || 0;
  const scopeName = `scope-${meeting.id}`;
  const scopeBox = meeting.body ? h("div", { class: "scope" },
    h("label", {}, h("input", { type: "radio", name: scopeName, value: "meeting", checked: true }), " This meeting"),
    h("label", { title: others ? "" : "Add another meeting of this body to use this" },
      h("input", { type: "radio", name: scopeName, value: "body", disabled: !others }),
      others ? ` All ${others + 1} meetings of ${meeting.body}` : ` All meetings of ${meeting.body} (add another to use this)`)) : null;
  const scope = () => (scopeBox ? scopeBox.querySelector("input:checked").value : "meeting");

  const askBtn = h("button", { class: "btn", type: "button" }, "Ask");
  const micBtn = h("button", { class: "btn mic", type: "button", "aria-label": "Ask by voice", title: "Ask by voice", html: MIC_ICON });
  const thinking = h("div", { class: "thinking", hidden: true });

  const busy = (on, msg = "") => { askBtn.disabled = micBtn.disabled = on && msg !== "listening"; thinking.hidden = !on; thinking.textContent = msg === "listening" ? "Listening… tap the mic again when you are done" : msg; };

  const ask = async (q) => {
    q = (q ?? textarea.value).trim();
    if (!q) return toast("Type or say a question first", true);
    busy(true, "Finding the answer in the meeting…");
    try {
      const a = await postJSON(`/api/meetings/${meeting.id}/ask`, { question: q, language: S.lang, scope: scope() });
      textarea.value = "";
      thread.prepend(qaCard(a));
    } catch (e) { toast(e.message, true); }
    busy(false);
  };
  askBtn.addEventListener("click", () => ask());
  textarea.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });

  let rec = null;
  micBtn.addEventListener("click", async () => {
    if (rec) { rec.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        micBtn.classList.remove("on");
        const type = rec.mimeType || "audio/webm";
        rec = null;
        const fd = new FormData();
        fd.append("audio", new Blob(chunks, { type }), type.includes("mp4") ? "question.mp4" : "question.webm");
        fd.append("language", S.lang);
        fd.append("scope", scope());
        busy(true, "Understanding your question…");
        try {
          const a = await api(`/api/meetings/${meeting.id}/ask-voice`, { method: "POST", body: fd });
          thread.prepend(qaCard(a, true));
        } catch (e) { toast(e.message, true); }
        busy(false);
      };
      rec.start();
      micBtn.classList.add("on");
      busy(true, "listening");
      setTimeout(() => rec && rec.state === "recording" && rec.stop(), 30000); // Saaras REST takes up to 30 s
    } catch (e) { toast("Microphone not available: " + e.message, true); }
  });

  const suggestions = h("div", { class: "suggest" }, (isLive ? SUGGEST.live : (SUGGEST[meeting.meeting_type] || SUGGEST.general)).map((q) =>
    h("button", { class: "chip", type: "button", onclick: () => ask(q) }, q)));

  [...(S.data.qa || [])].reverse().forEach((a) => thread.append(qaCard(a)));

  return h("aside", { class: "missed", "aria-label": "For people who missed the meeting" },
    h("h2", {}, "Missed the meeting?"),
    h("p", {}, "Hear what happened, or ask about anything. Every answer points to the moment it was said."),
    h("label", { class: "lbl", for: "langSel" }, "Your language"), langSel,
    briefBox,
    h("div", { class: "ask-box" }, textarea, h("div", { class: "ask-row" }, micBtn, askBtn), scopeBox, suggestions, thinking),
    thread);
}

function qaCard(a, autoplay = false) {
  const card = h("div", { class: `qa ${a.status}` });
  const refs = (a.refs || []).map((r) => {
    const local = r.meeting_id === S.data.meeting.id;
    return h("button", {
      class: "stamp", title: local ? "Play this moment" : "Open the other meeting at this moment",
      onclick: () => (local ? seekTo(r.seg) : (location.hash = `#/m/${r.meeting_id}?seg=${r.seg}`)),
      html: PLAY_ICON + `<span>${local ? hms(r.start) : `${r.label.split(" ")[0]} ${hms(r.start)}`}</span>`,
    });
  });
  const playBtn = h("button", { class: "play", type: "button" }, "Listen to this answer");
  playBtn.addEventListener("click", async () => {
    playBtn.disabled = true;
    playBtn.textContent = "Loading…";
    try {
      const res = await api(`/api/meetings/${S.data.meeting.id}/speak`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: a.answer, language: a.language_code }) });
      const url = URL.createObjectURL(await res.blob());
      const au = new Audio(url);
      au.play();
      playBtn.textContent = "Playing";
      au.onended = () => { playBtn.textContent = "Listen again"; playBtn.disabled = false; };
    } catch (e) { toast(e.message, true); playBtn.textContent = "Listen to this answer"; playBtn.disabled = false; }
  });
  card.append(
    h("div", { class: "q" }, a.question),
    h("p", { class: "a" }, a.answer),
    h("div", { class: "qa-foot" }, refs.length ? h("span", { class: "stamps" }, refs) : null, playBtn),
    a.follow_ups?.length ? h("div", { class: "suggest" }, a.follow_ups.map((f) =>
      h("button", { class: "chip", type: "button", onclick: (e) => { const ta = e.target.closest(".missed").querySelector("textarea"); ta.value = f; ta.focus(); } }, f))) : null);
  if (autoplay && S.config.has_key) playBtn.click();
  return card;
}

/* ---------------- share ---------------- */
function shareDialog(m) {
  const url = `${location.origin}/#/m/${m.id}/listen`;
  const input = h("input", { value: url, readonly: true, "aria-label": "Link" });
  const dlg = h("dialog", {},
    h("h2", { style: { fontFamily: "var(--serif)", fontWeight: 400, margin: "0 0 4px" } }, "Share with people who missed it"),
    h("p", { style: { color: "var(--ink-soft)", fontSize: "15px", marginTop: 0 } }, "Print this on the notice board or send it on WhatsApp. It opens the listen and ask page."),
    h("img", { src: `/api/meetings/${m.id}/qr`, alt: "QR code for the listen link" }),
    input,
    h("div", { style: { display: "flex", gap: "8px", justifyContent: "center", marginTop: "12px" } },
      h("button", { class: "btn", onclick: async () => { try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { input.select(); } } }, "Copy link"),
      h("a", { class: "btn ghost", href: `https://wa.me/?text=${encodeURIComponent(`${m.title}: listen to the summary or ask about it ${url}`)}`, target: "_blank", rel: "noopener" }, "WhatsApp"),
      h("button", { class: "btn ghost", onclick: () => dlg.close() }, "Close")));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}
