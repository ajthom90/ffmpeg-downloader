// ffmpeg_downloader/static/folder-picker.js

const folderInput = document.getElementById("outputFolder");
const folderStatus = document.getElementById("folderStatus");
const suggestions = document.getElementById("folderSuggestions");
const browseBtn = document.getElementById("browseBtn");
const modal = document.getElementById("folderModal");
const breadcrumb = document.getElementById("breadcrumb");
const folderList = document.getElementById("folderList");
const filterInput = document.getElementById("filterInput");
const searchInput = document.getElementById("searchInput");
const searchResults = document.getElementById("searchResults");
const newFolderName = document.getElementById("newFolderName");
const browseMode = document.getElementById("browseMode");
const searchMode = document.getElementById("searchMode");

let browseState = { currentPath: "", items: [], selectedPath: null };
let searchDebounce = null;
let validateDebounce = null;
let autocompleteDebounce = null;
let activeSuggestionIndex = -1;

// ---------------------------------------------------------------------------
// Live validation
// ---------------------------------------------------------------------------

folderInput.addEventListener("input", () => {
  clearTimeout(validateDebounce);
  validateDebounce = setTimeout(() => runValidation(folderInput.value), 200);
  clearTimeout(autocompleteDebounce);
  autocompleteDebounce = setTimeout(() => runAutocomplete(folderInput.value), 150);
});
folderInput.addEventListener("blur", () => {
  // Hide suggestions on blur unless the click landed on a suggestion.
  setTimeout(() => suggestions.setAttribute("hidden", ""), 120);
});
folderInput.addEventListener("focus", () => {
  if (folderInput.value) runAutocomplete(folderInput.value);
});

async function runValidation(path) {
  if (!path) {
    folderStatus.textContent = "";
    folderStatus.className = "folder-status";
    return;
  }
  try {
    const resp = await fetch(`/api/validate?path=${encodeURIComponent(path)}`);
    if (!resp.ok) throw new Error("invalid path");
    const v = await resp.json();
    if (v.exists && v.is_dir) {
      folderStatus.textContent = "✓";
      folderStatus.className = "folder-status ok";
      folderStatus.title = "Folder exists";
    } else if (!v.exists && v.writable) {
      folderStatus.textContent = "⚠";
      folderStatus.className = "folder-status warn";
      folderStatus.title = "Will be created";
    } else {
      folderStatus.textContent = "✕";
      folderStatus.className = "folder-status bad";
      folderStatus.title = "Path is not usable";
    }
  } catch {
    folderStatus.textContent = "✕";
    folderStatus.className = "folder-status bad";
    folderStatus.title = "Invalid path";
  }
}

// ---------------------------------------------------------------------------
// Inline autocomplete
// ---------------------------------------------------------------------------

async function runAutocomplete(prefix) {
  try {
    const resp = await fetch(`/api/autocomplete?prefix=${encodeURIComponent(prefix)}`);
    const body = await resp.json();
    renderSuggestions(body.matches || []);
  } catch {
    renderSuggestions([]);
  }
}

function renderSuggestions(matches) {
  suggestions.replaceChildren();
  if (matches.length === 0) {
    suggestions.setAttribute("hidden", "");
    activeSuggestionIndex = -1;
    return;
  }
  for (const m of matches) {
    const li = document.createElement("li");
    li.textContent = m.path;
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      folderInput.value = m.path + "/";
      suggestions.setAttribute("hidden", "");
      folderInput.focus();
      runValidation(folderInput.value);
      runAutocomplete(folderInput.value);
    });
    suggestions.appendChild(li);
  }
  suggestions.removeAttribute("hidden");
  activeSuggestionIndex = -1;
}

folderInput.addEventListener("keydown", (e) => {
  const items = suggestions.querySelectorAll("li");
  if (suggestions.hasAttribute("hidden") || items.length === 0) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeSuggestionIndex = Math.min(items.length - 1, activeSuggestionIndex + 1);
    highlightSuggestion(items);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeSuggestionIndex = Math.max(0, activeSuggestionIndex - 1);
    highlightSuggestion(items);
  } else if (e.key === "Enter" && activeSuggestionIndex >= 0) {
    e.preventDefault();
    items[activeSuggestionIndex].dispatchEvent(new MouseEvent("mousedown"));
  } else if (e.key === "Escape") {
    suggestions.setAttribute("hidden", "");
  } else if (e.key === "Tab" && activeSuggestionIndex >= 0) {
    e.preventDefault();
    items[activeSuggestionIndex].dispatchEvent(new MouseEvent("mousedown"));
  }
});

function highlightSuggestion(items) {
  items.forEach((li, i) => li.classList.toggle("active", i === activeSuggestionIndex));
}

// ---------------------------------------------------------------------------
// Modal — browse mode
// ---------------------------------------------------------------------------

browseBtn.addEventListener("click", () => openModal(folderInput.value));
document.getElementById("modalCloseBtn").addEventListener("click", closeModal);
document.getElementById("cancelFolderBtn").addEventListener("click", closeModal);
document.getElementById("selectFolderBtn").addEventListener("click", () => {
  folderInput.value = browseState.currentPath || "";
  closeModal();
  runValidation(folderInput.value);
});
document.getElementById("createFolderBtn").addEventListener("click", createNewFolder);
filterInput.addEventListener("input", () => renderFolderList(browseState.items));

document.querySelectorAll(".modal-tabs .tab").forEach((tab) => {
  tab.addEventListener("click", () => switchMode(tab.dataset.mode));
});

modal.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

function openModal(startPath) {
  modal.removeAttribute("hidden");
  filterInput.value = "";
  searchInput.value = "";
  switchMode("browse");
  loadFolder(sanitizeStart(startPath));
}

function closeModal() {
  modal.setAttribute("hidden", "");
}

function sanitizeStart(p) {
  if (!p) return "";
  return p.endsWith("/") ? p.slice(0, -1) : p;
}

async function loadFolder(path) {
  try {
    const resp = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    if (!resp.ok) {
      if (resp.status === 404 && path) return loadFolder("");
      const err = await resp.json();
      alert(err.error || "Failed to load folder");
      return;
    }
    const body = await resp.json();
    browseState = { currentPath: body.current_path, items: body.items, selectedPath: null };
    renderBreadcrumb(body.current_path);
    renderFolderList(body.items);
  } catch (e) {
    alert(`Browse failed: ${e.message}`);
  }
}

function renderBreadcrumb(path) {
  breadcrumb.replaceChildren();
  const root = document.createElement("span");
  root.className = "breadcrumb-item";
  root.textContent = "Root";
  root.addEventListener("click", () => loadFolder(""));
  breadcrumb.appendChild(root);
  if (!path) return;
  const parts = path.split("/");
  let acc = "";
  for (const seg of parts) {
    acc = acc ? `${acc}/${seg}` : seg;
    const sep = document.createElement("span");
    sep.className = "breadcrumb-sep";
    sep.textContent = "/";
    breadcrumb.appendChild(sep);
    const item = document.createElement("span");
    item.className = "breadcrumb-item";
    item.textContent = seg;
    const target = acc;
    item.addEventListener("click", () => loadFolder(target));
    breadcrumb.appendChild(item);
  }
}

function renderFolderList(items) {
  folderList.replaceChildren();
  const filter = filterInput.value.trim().toLowerCase();
  const folders = items
    .filter((i) => i.is_dir)
    .filter((i) => !filter || i.name.toLowerCase().includes(filter));
  if (folders.length === 0) {
    const li = document.createElement("li");
    li.className = "no-items";
    li.textContent = filter ? "No matches" : "No subfolders";
    folderList.appendChild(li);
    return;
  }
  for (const item of folders) {
    const li = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.textContent = "📁";
    const name = document.createElement("span");
    name.textContent = item.name;
    li.appendChild(icon);
    li.appendChild(name);
    li.addEventListener("dblclick", () => loadFolder(item.path));
    li.addEventListener("click", () => {
      folderList.querySelectorAll("li").forEach((el) => el.classList.remove("active"));
      li.classList.add("active");
      browseState.selectedPath = item.path;
    });
    folderList.appendChild(li);
  }
}

async function createNewFolder() {
  const name = newFolderName.value.trim();
  if (!name) return;
  try {
    const resp = await fetch("/api/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: browseState.currentPath, name }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      alert(err.error || "Create failed");
      return;
    }
    newFolderName.value = "";
    loadFolder(browseState.currentPath);
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// Modal — search mode
// ---------------------------------------------------------------------------

function switchMode(mode) {
  document.querySelectorAll(".modal-tabs .tab").forEach((t) => {
    const active = t.dataset.mode === mode;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (mode === "browse") {
    browseMode.removeAttribute("hidden");
    searchMode.setAttribute("hidden", "");
  } else {
    browseMode.setAttribute("hidden", "");
    searchMode.removeAttribute("hidden");
    searchInput.focus();
  }
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => runSearch(searchInput.value), 250);
});

async function runSearch(q) {
  searchResults.replaceChildren();
  if (!q) return;
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=50`);
    const body = await resp.json();
    if (!body.matches || body.matches.length === 0) {
      const li = document.createElement("li");
      li.className = "no-items";
      li.textContent = "No matches";
      searchResults.appendChild(li);
      return;
    }
    for (const m of body.matches) {
      const li = document.createElement("li");
      const icon = document.createElement("span");
      icon.className = "folder-icon";
      icon.textContent = "📁";
      const name = document.createElement("span");
      name.textContent = m.name;
      const hint = document.createElement("span");
      hint.className = "path-hint";
      hint.textContent = m.path;
      li.appendChild(icon);
      li.appendChild(name);
      li.appendChild(hint);
      li.addEventListener("click", () => {
        switchMode("browse");
        loadFolder(m.path);
      });
      searchResults.appendChild(li);
    }
    if (body.truncated) {
      const note = document.createElement("li");
      note.className = "no-items";
      note.textContent = "More results truncated — refine your query.";
      searchResults.appendChild(note);
    }
  } catch (e) {
    alert(`Search failed: ${e.message}`);
  }
}
