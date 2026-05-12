// ffmpeg_downloader/static/app.js
// Vanilla JS, ES modules, no framework.

import "./folder-picker.js";
import { getSelectedTrackUrls } from "./resolution-picker.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  jobs: {},                  // jobId -> job object
  perJobStreams: new Map(),  // jobId -> EventSource
  globalStream: null,
};

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const form = $("downloadForm");
const urlInput = $("urlInput");
const urlHint = $("urlHint");
const resolutionGroup = $("resolutionGroup");
const resolutionSelect = $("resolutionSelect");
const jobsList = $("jobsList");

// ---------------------------------------------------------------------------
// Job rendering
// ---------------------------------------------------------------------------

function jobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.dataset.jobId = job.id;

  const header = document.createElement("div");
  header.className = "job-header";

  const filename = document.createElement("div");
  filename.className = "job-filename";
  filename.textContent = job.filename;

  const right = document.createElement("div");
  right.style.display = "flex";
  right.style.gap = "8px";
  right.style.alignItems = "center";

  const status = document.createElement("span");
  status.className = `job-status ${job.status}`;
  status.textContent = job.status;
  right.appendChild(status);

  if (job.status === "queued" || job.status === "running") {
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "cancel-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => cancelJob(job.id));
    right.appendChild(cancelBtn);
  }

  header.appendChild(filename);
  header.appendChild(right);
  card.appendChild(header);

  const progress = document.createElement("div");
  progress.className = "progress";
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  if (job.progress === null && (job.status === "running" || job.status === "queued")) {
    bar.classList.add("indeterminate");
  } else {
    const pct = job.progress != null ? job.progress : (job.status === "completed" ? 100 : 0);
    bar.style.width = `${pct}%`;
    bar.textContent = `${pct.toFixed ? pct.toFixed(1) : pct}%`;
  }
  progress.appendChild(bar);
  card.appendChild(progress);

  if (job.duration_seconds || job.current_time_seconds || job.speed) {
    const meta = document.createElement("div");
    meta.className = "job-meta";
    const left = document.createElement("span");
    const cur = fmtSeconds(job.current_time_seconds);
    const tot = job.duration_seconds ? `/${fmtSeconds(job.duration_seconds)}` : "";
    left.textContent = `${cur}${tot}`;
    const rightMeta = document.createElement("span");
    rightMeta.textContent = job.speed || "";
    meta.appendChild(left);
    meta.appendChild(rightMeta);
    card.appendChild(meta);
  }

  if (job.message && (job.status === "failed")) {
    const err = document.createElement("div");
    err.className = "job-error";
    err.textContent = job.message.slice(0, 400);
    card.appendChild(err);
  }

  const cmd = document.createElement("div");
  cmd.className = "job-command";
  cmd.textContent = job.command;
  card.appendChild(cmd);

  return card;
}

function fmtSeconds(s) {
  if (s == null) return "—";
  const total = Math.floor(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

function renderJobs() {
  jobsList.replaceChildren();
  const list = Object.values(state.jobs).sort(
    (a, b) => (b.created_at || 0) - (a.created_at || 0),
  );
  if (list.length === 0) {
    const empty = document.createElement("div");
    empty.className = "no-jobs";
    empty.textContent = "No downloads yet";
    jobsList.appendChild(empty);
    return;
  }
  for (const job of list) jobsList.appendChild(jobCard(job));
}

function upsertJob(job) {
  state.jobs[job.id] = { ...state.jobs[job.id], ...job };
  renderJobs();
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  const text = await resp.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!resp.ok) {
    const msg = (payload && payload.error) || `${resp.status} ${resp.statusText}`;
    const err = new Error(msg);
    err.status = resp.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

// ---------------------------------------------------------------------------
// Form submit
// ---------------------------------------------------------------------------

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const audioUrls = getSelectedTrackUrls("audio");
  const subtitleUrls = getSelectedTrackUrls("subtitle");
  // Multi-input mux requires a specific video variant. If the user wants
  // separate audio/subtitle streams but left the video selector on "Auto",
  // pick the highest-bitrate variant for them (its URL is the first concrete
  // option in the dropdown).
  let videoUrl = resolutionSelect.value || null;
  let videoLabel = videoUrl
    ? resolutionSelect.options[resolutionSelect.selectedIndex].textContent
    : null;
  if (!videoUrl && (audioUrls.length || subtitleUrls.length)) {
    const firstConcrete = [...resolutionSelect.options].find((o) => o.value);
    if (firstConcrete) {
      videoUrl = firstConcrete.value;
      videoLabel = firstConcrete.textContent;
    }
  }
  const body = {
    url: urlInput.value.trim(),
    selected_variant_url: videoUrl,
    selected_variant_label: videoLabel,
    audio_urls: audioUrls,
    subtitle_urls: subtitleUrls,
    filename: $("filenameInput").value.trim(),
    extension: $("extensionSelect").value,
    codec: $("codecSelect").value,
    output_folder: $("outputFolder").value.trim(),
  };
  try {
    const job = await api("POST", "/api/downloads", body);
    upsertJob(job);
    attachJobStream(job.id);
    urlInput.value = "";
    // Re-fire input so resolution-picker (loaded as a separate module) clears itself.
    urlInput.dispatchEvent(new Event("input"));
    $("filenameInput").value = "";
  } catch (err) {
    alert(`Failed to start download: ${err.message}`);
  }
});

async function cancelJob(jobId) {
  try {
    await api("DELETE", `/api/downloads/${encodeURIComponent(jobId)}`);
  } catch (err) {
    alert(`Cancel failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

function attachJobStream(jobId) {
  if (state.perJobStreams.has(jobId)) return;
  const es = new EventSource(`/api/downloads/${encodeURIComponent(jobId)}/events`);
  state.perJobStreams.set(jobId, es);
  es.addEventListener("progress", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob({ id: jobId, ...data });
  });
  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob(data);
    if (["completed", "failed", "cancelled"].includes(data.status)) {
      es.close();
      state.perJobStreams.delete(jobId);
    }
  });
  es.onerror = () => { /* let the connection retry */ };
}

function attachGlobalStream() {
  if (state.globalStream) return;
  const es = new EventSource("/api/events");
  state.globalStream = es;
  es.addEventListener("job", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob(data);
  });
}

// ---------------------------------------------------------------------------
// Init: load existing jobs and start streams
// ---------------------------------------------------------------------------

(async function init() {
  try {
    const existing = await api("GET", "/api/downloads?limit=50");
    for (const j of existing) {
      state.jobs[j.id] = j;
      if (j.status === "queued" || j.status === "running") attachJobStream(j.id);
    }
    renderJobs();
    attachGlobalStream();
  } catch (err) {
    console.error("Failed to load existing jobs", err);
  }
})();
