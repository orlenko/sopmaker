"use strict";
const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
let state, focused, dirty = false, searchTimer, importing = false, detailSession = 0;
const selected = new Set();
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.has("token")) sessionStorage.setItem("sopmaker-token", hash.get("token"));
history.replaceState(null, "", "/");
const token = sessionStorage.getItem("sopmaker-token") || "";

function message(text, error = false) {
  $("message").hidden = false;
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
async function api(path, body, method = "POST") {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), body instanceof File ? 300000 : 30000);
  try {
    const response = await fetch(path, {method: body === undefined ? "GET" : method, signal: controller.signal,
      headers: {Authorization: `Bearer ${token}`, ...(body instanceof File ? {} : {"Content-Type": "application/json"})},
      body: body === undefined ? undefined : body instanceof File ? body : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The request failed.");
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The local request timed out. Your edits remain in this window; check saved revisions before retrying a save.");
    throw error;
  } finally { clearTimeout(timer); }
}
async function action(callback) {
  try { return await callback(); } catch (error) { message(error.message, true); }
}
function view(name) {
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${name}`;
  for (const button of document.querySelectorAll(".nav")) button.classList.toggle("active", button.dataset.view === name);
}
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => view(button.dataset.view));
$("add-files").onclick = () => view("evidence");
$("choose-files").onclick = () => $("file-picker").click();
$("choose-folder").onclick = () => $("folder-picker").click();

async function importFiles(files) {
  if (importing) return;
  importing = true;
  const supported = /\.(jpe?g|png|webp|mp4|mov|m4v|webm|mkv|avi|m4a|mp3|wav|aac|ogg|flac)$/i;
  let count = 0, duplicates = 0, skipped = 0;
  try {
    for (const file of files) {
      if (!supported.test(file.name)) { skipped++; continue; }
      message(`Copying locally: ${file.name} (${count + duplicates + 1}/${files.length})`);
      const result = await api(`/api/files?name=${encodeURIComponent(file.name)}`, file, "PUT");
      result.imported ? count++ : duplicates++;
    }
    message(`Imported ${count} file(s) locally. ${duplicates} duplicate(s), ${skipped} unsupported file(s) skipped. Nothing sent to OpenAI yet.`);
    await refresh();
  } finally { importing = false; }
}
for (const id of ["file-picker", "folder-picker"]) $(id).onchange = event => action(() => importFiles(Array.from(event.target.files)));

async function refresh() {
  const query = new URLSearchParams({q: $("search").value});
  if ($("show-archived").checked) query.set("archived", "1");
  state = await api("/api/state?" + query);
  $("workspace-name").textContent = state.settings.name;
  $("note-count").textContent = state.observations.length;
  $("file-count").textContent = state.assets.length;
  $("worker-status").textContent = state.progress.running ? `Processing ${state.progress.current || "…"}` : "Ready · saved locally";
  $("process").disabled = state.progress.running;
  $("cancel").hidden = !state.progress.running;
  $("key-status").textContent = state.credentials.configured ? `Key ready · ${state.credentials.origin.replaceAll("_", " ")}` : "No key configured";
  if (!$("settings-form").dataset.loaded) {
    for (const [key, value] of Object.entries(state.settings)) if ($("settings-form").elements[key]) $("settings-form").elements[key].value = value;
    $("settings-form").dataset.loaded = "yes";
  }
  renderNotes(); renderFiles();
}
function captureLabel(asset) {
  const time = asset.prepared?.capture_time;
  return time?.utc ? `${new Date(time.utc).toLocaleString()} · ${time.basis.replaceAll("_", " ")}` : "Capture time unknown";
}
function renderNotes() {
  const container = $("notes-list"); container.replaceChildren();
  const filter = $("state-filter").value;
  const notes = state.observations.filter(o => filter === "all" || o.state === filter).sort((a, b) => {
    const time = o => o.evidence?.prepared?.capture_time?.utc || "9999";
    return time(a).localeCompare(time(b)) || a.id.localeCompare(b.id);
  });
  if (!notes.length) {
    const empty = node("div", undefined, "empty");
    empty.append(node("h2", state.assets.length ? "Your observations will appear here" : "Start with an inspection fragment"), node("p", state.assets.length ? "Open Source files to process new evidence, or adjust your search." : "A photo, a short narrated video, or a voice note is enough."));
    const button = node("button", "Add evidence", "primary"); button.onclick = () => view("evidence"); empty.append(button); container.append(empty);
  }
  for (const note of notes) {
    const asset = boundAsset(note, state.assets.find(a => a.id === note.asset_id));
    const card = node("div", undefined, "note-card" + (note.id === focused ? " selected" : ""));
    const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.className = "select-note"; checkbox.checked = selected.has(note.id);
    checkbox.setAttribute("aria-label", `Select ${note.generated.title} for export`);
    checkbox.onchange = () => { checkbox.checked ? selected.add(note.id) : selected.delete(note.id); selectionCount(); };
    const open = node("button", undefined, "open-note");
    open.append(node("span", note.generated.title, "note-title"), node("p", note.note.slice(0, 210), "note-preview"));
    const meta = node("div", undefined, "meta"); meta.append(node("span", note.state, `badge ${note.state}`), node("span", asset.name), node("span", note.archived ? "Previous draft" : captureLabel(asset))); open.append(meta);
    open.onclick = () => {
      if (dirty && !confirm("Discard unsaved changes to this note?")) return;
      focused = note.id; dirty = false; renderNotes(); renderDetail(note, asset);
    };
    card.append(checkbox, open); container.append(card);
  }
  selectionCount();
}
function selectionCount() { $("selection-count").textContent = `${selected.size} note(s) selected`; }
function boundAsset(note, asset) {
  return {...asset, prepared: note.evidence?.prepared || null, transcript: note.evidence?.transcript || null,
    evidence_id: note.evidence?.id, provenance: note.evidence?.provenance || "unavailable"};
}
function mediaURL(asset, filename) {
  const suffix = asset.evidence_id ? `?evidence=${encodeURIComponent(asset.evidence_id)}` : "";
  return `/media/${asset.id}/${encodeURIComponent(filename)}` + suffix;
}
function renderTranscript(body, asset) {
  const chunks = asset.transcript?.chunks || [];
  body.append(node("h3", "Original transcript"));
  if (!chunks.length) body.append(node("p", "No completed transcript is available for this evidence revision.", "small"));
  chunks.forEach(c => body.append(node("p", `[${c.start.toFixed(1)}–${c.end.toFixed(1)}s, coarse chunk range]\n${c.text}`, "transcript")));
}
function renderEvidence(body, asset) {
  if (asset.provenance && asset.provenance !== "bound") body.append(node("p", "Legacy input association is unverified. Earlier generation inputs could not be recovered.", "file-error"));
  const images = asset.prepared?.images || [];
  if (images.length) {
    const main = node("img"); main.className = "evidence-image"; main.src = mediaURL(asset, images[0].file); main.alt = "Original evidence preview"; body.append(main);
    const strip = node("div", undefined, "image-strip");
    for (const image of images) {
      const button = node("button"); const thumb = node("img"); thumb.src = mediaURL(asset, image.file); thumb.alt = image.seconds === null ? "Photo" : `Frame at ${image.seconds} seconds`;
      button.append(thumb, node("span", image.seconds === null ? "Photo" : `${image.seconds}s`)); button.onclick = () => { main.src = thumb.src; main.alt = thumb.alt; }; strip.append(button);
    }
    body.append(strip);
  }
  const source = node("p", undefined, "source-label"); const link = node("a", asset.name); link.href = mediaURL(asset, "original"); link.target = "_blank"; link.rel = "noopener"; source.append("Original: ", link, node("br"), captureLabel(asset)); body.append(source);
  const kind = asset.prepared?.kind || (/\.(jpe?g|png|webp)$/i.test(asset.name) ? "photo" : /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(asset.name) ? "video" : "audio");
  if (kind !== "photo") {
    const player = node(kind === "video" ? "video" : "audio"); player.controls = true; player.src = link.href; player.preload = "metadata"; body.append(player);
  } else if (!images.length) {
    const original = node("img"); original.className = "evidence-image"; original.src = link.href; original.alt = "Original source photo"; body.append(original);
  }
}
function renderDetail(note, currentAsset) {
  const session = ++detailSession;
  let editGeneration = 0;
  const asset = boundAsset(note, currentAsset);
  const detail = $("detail"); detail.replaceChildren();
  const head = node("div", undefined, "detail-head"); head.append(node("span", note.state, `badge ${note.state}`), node("h2", note.generated.title)); detail.append(head);
  const body = node("div", undefined, "detail-body");
  renderEvidence(body, asset);
  body.append(node("h3", "Observation note"));
  const label = node("label", "Edit the note before using it in your report"); label.htmlFor = "note-editor"; body.append(label);
  const editor = node("textarea"); editor.id = "note-editor"; editor.rows = 6; editor.value = note.note; body.append(editor);
  const status = node("p", "Draft text is AI-generated. Your changes are saved only when you select Save.", "save-status"); body.append(status);
  const changed = () => { editGeneration++; dirty = true; status.textContent = "Unsaved changes"; };
  editor.oninput = changed;
  const reviewer = node("input"); reviewer.placeholder = "Reviewer name"; reviewer.setAttribute("aria-label", "Reviewer name"); reviewer.value = note.reviewer || "";
  const reviewed = node("input"); reviewed.type = "checkbox"; reviewed.checked = note.state === "reviewed";
  const reviewedLabel = node("label", " Reviewed"); reviewedLabel.prepend(reviewed);
  const row = node("div", undefined, "review-row"); row.append(reviewer, reviewedLabel); body.append(row);
  reviewer.oninput = reviewed.onchange = changed;
  const save = node("button", "Save note", "primary"); save.onclick = () => action(async () => {
    const submittedGeneration = editGeneration;
    const snapshot = {id: note.id, revision: note.revision, note: editor.value, state: reviewed.checked ? "reviewed" : "draft", reviewer: reviewer.value};
    save.disabled = true;
    try {
      const result = await api("/api/edit", snapshot);
      // Update the revision token without touching any newer text or another editor.
      note.revision = result.revision;
      await refresh();
      if (session === detailSession && focused === note.id) {
        if (editGeneration === submittedGeneration) {
          dirty = false; renderDetail(result, state.assets.find(a => a.id === result.asset_id));
        } else {
          dirty = true; status.textContent = "Earlier edit saved. Your newer changes are still unsaved.";
        }
      }
      message(`Note saved: ${result.generated.title} (revision ${result.revision}). The original draft and previous edits are preserved.`);
    } finally { save.disabled = false; }
  }); body.append(save);
  for (const [heading, values] of [["Follow-up", note.generated.follow_ups], ["Open questions", note.generated.open_questions]]) {
    if (values.length) { body.append(node("h3", heading)); const list = node("ul"); values.forEach(v => list.append(node("li", v))); body.append(list); }
  }
  body.append(node("h3", "Source-linked claims"));
  for (const claim of note.generated.claims) {
    const item = node("div", claim.text, "claim"); item.append(node("small", `${claim.basis.replaceAll("_", " ")} · ${claim.evidence.map(e => e.source_id).join(", ")}`));
    for (const reference of claim.evidence) if (reference.quote) item.append(node("p", `“${reference.quote}”`)); body.append(item);
  }
  renderTranscript(body, asset);
  const history = node("button", "View previous saved revisions", "text-button"); history.onclick = () => action(async () => {
    const revisions = await api(`/api/history/${note.id}`); const panel = node("div");
    revisions.forEach(r => panel.append(node("h3", `Revision ${r.revision} · ${r.state}`), node("p", r.note, "transcript")));
    if (!revisions.length) panel.append(node("p", "No previous saved revisions.")); history.replaceWith(panel);
  }); body.append(node("br"), history); detail.append(body);
}
function renderFiles() {
  const list = $("files-list"); list.replaceChildren();
  if (!state.assets.length) list.append(node("p", "No source files imported yet."));
  const needle = $("file-search").value.trim().toLocaleLowerCase();
  const assets = state.assets.filter(asset => !needle || (asset.name + " " + (asset.transcript?.chunks || []).map(chunk => chunk.text).join(" ")).toLocaleLowerCase().includes(needle));
  if (needle && !assets.length) list.append(node("p", "No matching source files or narration."));
  for (const asset of assets) {
    const row = node("div", undefined, "file-row"), information = node("div");
    information.append(node("div", asset.name, "file-name"), node("div", `${asset.state} · ${captureLabel(asset)}`, "file-state"));
    if (asset.error) information.append(node("p", asset.error, "file-error")); row.append(information);
    const open = node("button", "Review source", "secondary"); open.setAttribute("aria-label", `Review source ${asset.name}`);
    open.onclick = () => {
      const body = $("source-detail"); body.hidden = false; body.replaceChildren(node("h2", asset.name), node("p", `Processing state: ${asset.state}`, "small"));
      if (asset.error) body.append(node("p", asset.error, "file-error"));
      renderEvidence(body, asset); renderTranscript(body, asset); body.scrollIntoView({block: "start", behavior: "smooth"});
    }; row.append(open);
    if (["error", "interrupted", "ready"].includes(asset.state)) {
      const retry = node("button", asset.state === "ready" ? "Update draft" : "Retry explicitly", "text-button"); retry.disabled = state.progress.running;
      retry.onclick = () => action(async () => { await api("/api/process", {asset_ids: [asset.id]}); message("Processing requested. Unchanged completed stages will be reused."); await refresh(); }); row.append(retry);
    }
    list.append(row);
  }
}
$("process").onclick = () => action(async () => {
  if (!state.credentials.configured) { view("setup"); throw new Error("Add your OpenAI API key before processing."); }
  if (!state.assets.some(a => a.state === "imported")) throw new Error("Import a new file first, or use an individual file's update/retry action.");
  await api("/api/process", {}); message("Drafting notes. You can keep reviewing existing observations."); await refresh();
});
$("cancel").onclick = () => action(async () => { await api("/api/cancel", {}); message("Cancellation requested. The active stage may finish before processing stops."); });
$("settings-form").onsubmit = event => { event.preventDefault(); action(async () => { await api("/api/settings", Object.fromEntries(new FormData(event.target))); await refresh(); message("Notebook details saved."); }); };
$("key-form").onsubmit = event => { event.preventDefault(); action(async () => {
  const key = $("api-key").value; $("api-key").value = "";
  await api("/api/key", {key, remember: $("remember-key").checked}); await refresh(); message("Key configured. It will be checked when you explicitly process evidence.");
}); };
for (const [id, path] of [["load-key", "load"], ["clear-key", "clear"], ["forget-key", "forget"]]) $(id).onclick = () => action(async () => { await api(`/api/key/${path}`, {}); await refresh(); message("Credential setting updated."); });
$("export").onclick = () => action(async () => {
  const result = await api("/api/export", {ids: [...selected], include_drafts: $("include-drafts").checked});
  const download = node("a"); download.href = result.url; download.download = ""; download.click(); message("Export ready: editable Word notes, CSV, JSON, and an evidence index with originals.");
});
$("search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => action(refresh), 250); };
$("state-filter").onchange = () => renderNotes(); $("show-archived").onchange = () => action(refresh);
$("file-search").oninput = () => renderFiles();
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
action(async () => { await api("/api/session", {}); await refresh(); if (!state.credentials.configured && !state.assets.length) view("setup"); });
setInterval(() => { if (state?.progress.running && !importing) action(refresh); }, 2000);
