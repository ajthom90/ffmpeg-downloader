// ffmpeg_downloader/static/resolution-picker.js

const urlInput = document.getElementById("urlInput");
const urlHint = document.getElementById("urlHint");
const resolutionGroup = document.getElementById("resolutionGroup");
const resolutionSelect = document.getElementById("resolutionSelect");
const audioGroup = document.getElementById("audioGroup");
const audioList = document.getElementById("audioList");
const subtitleGroup = document.getElementById("subtitleGroup");
const subtitleList = document.getElementById("subtitleList");

let probeDebounce = null;
let lastProbedUrl = "";

urlInput.addEventListener("input", () => {
  clearTimeout(probeDebounce);
  const value = urlInput.value.trim();
  if (!value || !/^https?:\/\//i.test(value)) {
    hideAllPickers();
    urlHint.textContent = "";
    lastProbedUrl = "";
    return;
  }
  probeDebounce = setTimeout(() => probe(value), 500);
});

export function hideAllPickers() {
  resolutionGroup.setAttribute("hidden", "");
  resolutionSelect.replaceChildren();
  audioGroup.setAttribute("hidden", "");
  audioList.replaceChildren();
  subtitleGroup.setAttribute("hidden", "");
  subtitleList.replaceChildren();
}

// Back-compat alias for app.js callers.
export const hideResolutionGroup = hideAllPickers;

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
      return;
    }
    if (body.type === "hls_master" && body.variants && body.variants.length > 0) {
      populateResolutions(body.variants);
      populateTracks(audioGroup, audioList, body.audio_tracks || [], "audio");
      populateTracks(subtitleGroup, subtitleList, body.subtitle_tracks || [], "subtitle");
      const bits = [`${body.variants.length} video`];
      if ((body.audio_tracks || []).length) bits.push(`${body.audio_tracks.length} audio`);
      if ((body.subtitle_tracks || []).length) bits.push(`${body.subtitle_tracks.length} subtitle`);
      urlHint.textContent = `HLS master playlist — ${bits.join(", ")} track(s) available`;
    } else if (body.type === "hls_media") {
      hideAllPickers();
      urlHint.textContent = "HLS media playlist — single resolution";
    } else if (body.type === "direct") {
      hideAllPickers();
      urlHint.textContent = "Direct media URL";
    } else {
      hideAllPickers();
      urlHint.textContent = body.message || "Could not inspect URL";
    }
  } catch (e) {
    hideAllPickers();
    urlHint.textContent = `Inspect failed: ${e.message}`;
  }
}

function populateResolutions(variants) {
  resolutionSelect.replaceChildren();
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
