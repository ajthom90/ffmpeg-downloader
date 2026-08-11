// ffmpeg_downloader/static/resolution-picker.js

const urlInput = document.getElementById("urlInput");
const urlHint = document.getElementById("urlHint");
const resolutionGroup = document.getElementById("resolutionGroup");
const resolutionSelect = document.getElementById("resolutionSelect");
const resolutionLabel = document.getElementById("resolutionLabel");
const audioGroup = document.getElementById("audioGroup");
const audioList = document.getElementById("audioList");
const subtitleGroup = document.getElementById("subtitleGroup");
const subtitleList = document.getElementById("subtitleList");
const codecSelect = document.getElementById("codecSelect");
const codecHint = document.getElementById("codecHint");
const submitBtn = document.getElementById("submitBtn");

let probeDebounce = null;
let lastProbedUrl = "";
/** @type {"ffmpeg" | "ytdlp" | null} */
let probeMode = null;

urlInput.addEventListener("input", () => {
  clearTimeout(probeDebounce);
  const value = urlInput.value.trim();
  if (!value || !/^https?:\/\//i.test(value)) {
    hideAllPickers();
    urlHint.textContent = "";
    lastProbedUrl = "";
    setCodecEnabled(true);
    setSubmitBlocked(false);
    probeMode = null;
    return;
  }
  probeDebounce = setTimeout(() => probe(value), 500);
});

export function hideAllPickers() {
  resolutionGroup.setAttribute("hidden", "");
  resolutionSelect.replaceChildren();
  if (resolutionLabel) resolutionLabel.textContent = "Video resolution";
  audioGroup.setAttribute("hidden", "");
  audioList.replaceChildren();
  subtitleGroup.setAttribute("hidden", "");
  subtitleList.replaceChildren();
}

// Back-compat alias for app.js callers.
export const hideResolutionGroup = hideAllPickers;

export function getDownloadBackend() {
  return probeMode === "ytdlp" ? "ytdlp" : "ffmpeg";
}

export function getSelectedFormat() {
  if (probeMode !== "ytdlp") return null;
  const opt = resolutionSelect.selectedOptions[0];
  if (!opt) {
    return { format_selector: "bv*+ba/b", format_label: "Best available" };
  }
  return {
    format_selector: opt.value || "bv*+ba/b",
    format_label: opt.dataset.label || opt.textContent,
  };
}

function setCodecEnabled(on) {
  if (codecSelect) codecSelect.disabled = !on;
  if (codecHint) {
    if (on) codecHint.setAttribute("hidden", "");
    else codecHint.removeAttribute("hidden");
  }
}

function setSubmitBlocked(blocked) {
  if (submitBtn) submitBtn.disabled = !!blocked;
}

async function probe(url) {
  if (url === lastProbedUrl) return;
  lastProbedUrl = url;
  urlHint.textContent = "Inspecting URL…";
  try {
    const resp = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      urlHint.textContent = body.error || "Inspect failed";
      hideAllPickers();
      setCodecEnabled(true);
      setSubmitBlocked(false);
      probeMode = null;
      return;
    }
    if (body.type === "extractor" && body.formats && body.formats.length) {
      populateExtractorFormats(body.formats);
      urlHint.textContent = body.extractor
        ? `Detected ${body.extractor} — choose quality`
        : "Site video — choose quality";
      const filenameInput = document.getElementById("filenameInput");
      if (filenameInput && body.title && !filenameInput.value.trim()) {
        filenameInput.value = body.title;
      }
      setCodecEnabled(false);
      setSubmitBlocked(false);
      probeMode = "ytdlp";
    } else if (body.type === "unsupported") {
      hideAllPickers();
      setCodecEnabled(true);
      urlHint.textContent = body.message || "Unsupported URL";
      setSubmitBlocked(true);
      probeMode = null;
    } else if (body.type === "hls_master" && body.variants && body.variants.length > 0) {
      populateResolutions(body.variants);
      populateTracks(audioGroup, audioList, body.audio_tracks || [], "audio");
      populateTracks(subtitleGroup, subtitleList, body.subtitle_tracks || [], "subtitle");
      const bits = [`${body.variants.length} video`];
      if ((body.audio_tracks || []).length) bits.push(`${body.audio_tracks.length} audio`);
      if ((body.subtitle_tracks || []).length) bits.push(`${body.subtitle_tracks.length} subtitle`);
      urlHint.textContent = `HLS master playlist — ${bits.join(", ")} track(s) available`;
      setCodecEnabled(true);
      setSubmitBlocked(false);
      probeMode = "ffmpeg";
    } else if (body.type === "hls_media") {
      hideAllPickers();
      urlHint.textContent = "HLS media playlist — single resolution";
      setCodecEnabled(true);
      setSubmitBlocked(false);
      probeMode = "ffmpeg";
    } else if (body.type === "direct") {
      hideAllPickers();
      urlHint.textContent = "Direct media URL";
      setCodecEnabled(true);
      setSubmitBlocked(false);
      probeMode = "ffmpeg";
    } else {
      hideAllPickers();
      urlHint.textContent = body.message || "Could not inspect URL";
      setCodecEnabled(true);
      setSubmitBlocked(false);
      probeMode = "ffmpeg";
    }
  } catch (e) {
    hideAllPickers();
    urlHint.textContent = `Inspect failed: ${e.message}`;
    setCodecEnabled(true);
    setSubmitBlocked(false);
    probeMode = null;
  }
}

function populateExtractorFormats(formats) {
  resolutionSelect.replaceChildren();
  if (resolutionLabel) resolutionLabel.textContent = "Quality";
  for (const f of formats) {
    const o = document.createElement("option");
    o.value = f.format_selector || "";
    o.textContent = f.label || f.id || "format";
    o.dataset.label = f.label || f.id || "";
    resolutionSelect.appendChild(o);
  }
  audioGroup.setAttribute("hidden", "");
  audioList.replaceChildren();
  subtitleGroup.setAttribute("hidden", "");
  subtitleList.replaceChildren();
  resolutionGroup.removeAttribute("hidden");
}

function populateResolutions(variants) {
  resolutionSelect.replaceChildren();
  if (resolutionLabel) resolutionLabel.textContent = "Video resolution";
  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = `Auto (highest — ${variants[0].label})`;
  resolutionSelect.appendChild(auto);
  for (const v of variants) {
    const o = document.createElement("option");
    o.value = v.url;
    o.textContent = v.label + (v.codecs ? `  ${v.codecs.split(",")[0]}` : "");
    resolutionSelect.appendChild(o);
  }
  resolutionGroup.removeAttribute("hidden");
}

function populateTracks(groupEl, listEl, tracks, kind) {
  listEl.replaceChildren();
  if (!tracks.length) {
    groupEl.setAttribute("hidden", "");
    return;
  }
  for (const t of tracks) {
    const id = `${kind}_${listEl.children.length}`;
    const wrap = document.createElement("label");
    wrap.htmlFor = id;
    wrap.dataset.url = t.url;
    wrap.dataset.kind = kind;

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = id;
    cb.dataset.url = t.url;
    // Audio: default-on only for DEFAULT=YES tracks. Subtitles: always off.
    cb.checked = kind === "audio" && !!t.default;

    const labelText = document.createElement("span");
    labelText.textContent = t.label || t.name || t.language || "track";

    const meta = document.createElement("span");
    meta.className = "track-meta";
    const metaBits = [];
    if (t.language && t.language !== (t.name || "")) metaBits.push(t.language);
    if (t.codecs) metaBits.push(t.codecs.split(",")[0]);
    if (t.channels) metaBits.push(`${t.channels}ch`);
    if (t.default) metaBits.push("default");
    meta.textContent = metaBits.join(" · ");

    wrap.appendChild(cb);
    wrap.appendChild(labelText);
    wrap.appendChild(meta);
    listEl.appendChild(wrap);
  }
  groupEl.removeAttribute("hidden");
}

export function getSelectedTrackUrls(kind) {
  const list = kind === "audio" ? audioList : subtitleList;
  return [...list.querySelectorAll("input[type='checkbox']")]
    .filter((cb) => cb.checked)
    .map((cb) => cb.dataset.url);
}
