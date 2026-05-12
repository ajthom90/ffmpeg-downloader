// ffmpeg_downloader/static/resolution-picker.js

const urlInput = document.getElementById("urlInput");
const urlHint = document.getElementById("urlHint");
const resolutionGroup = document.getElementById("resolutionGroup");
const resolutionSelect = document.getElementById("resolutionSelect");

let probeDebounce = null;
let lastProbedUrl = "";

urlInput.addEventListener("input", () => {
  clearTimeout(probeDebounce);
  const value = urlInput.value.trim();
  if (!value || !/^https?:\/\//i.test(value)) {
    hideResolutionGroup();
    urlHint.textContent = "";
    lastProbedUrl = "";
    return;
  }
  probeDebounce = setTimeout(() => probe(value), 500);
});

export function hideResolutionGroup() {
  resolutionGroup.setAttribute("hidden", "");
  resolutionSelect.replaceChildren();
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
      hideResolutionGroup();
      return;
    }
    if (body.type === "hls_master" && body.variants && body.variants.length > 0) {
      populateResolutions(body.variants);
      urlHint.textContent = `HLS master playlist — ${body.variants.length} variants available`;
    } else if (body.type === "hls_media") {
      hideResolutionGroup();
      urlHint.textContent = "HLS media playlist — single resolution";
    } else if (body.type === "direct") {
      hideResolutionGroup();
      urlHint.textContent = "Direct media URL";
    } else {
      hideResolutionGroup();
      urlHint.textContent = body.message || "Could not inspect URL";
    }
  } catch (e) {
    hideResolutionGroup();
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
