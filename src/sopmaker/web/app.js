"use strict";
const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
let state, focused, dirty = false, searchTimer, importing = false, detailSession = 0;
let lastNoteSignature, lastFileSignature;
const selected = new Set();
const hash = new URLSearchParams(location.hash.slice(1));
if (hash.has("token")) sessionStorage.setItem("sopmaker-token", hash.get("token"));
history.replaceState(null, "", "/");
const token = sessionStorage.getItem("sopmaker-token") || "";

function message(text, error = false, topic = "action") {
  $("message").hidden = false;
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  $("message").dataset.topic = topic;
}
async function api(path, body, method = "POST") {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), body instanceof File ? 300000 : 30000);
  try {
    const response = await fetch(path, {method: body === undefined ? "GET" : method, signal: controller.signal,
      headers: {Authorization: `Bearer ${token}`, ...(body instanceof File ? {} : {"Content-Type": "application/json"})},
      body: body === undefined ? undefined : body instanceof File ? body : JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "The request failed."); error.status = response.status; throw error; }
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
  activeView = name;
  for (const section of document.querySelectorAll(".view")) section.hidden = section.id !== `view-${name}`;
  for (const button of document.querySelectorAll(".nav")) { button.classList.toggle("active", button.dataset.view === name); if (button.dataset.view === name) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); }
  renderRun();
}
document.querySelectorAll("[data-view]").forEach(button => button.onclick = () => view(button.dataset.view));
$("add-files").onclick = () => view("evidence");
$("choose-files").onclick = () => $("file-picker").click();
$("choose-folder").onclick = () => $("folder-picker").click();

async function importFiles(files) {
  if (importing) return;
  importing = true;
  $("generate-report").disabled = true;
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
  } finally {
    importing = false;
    $("file-picker").value = $("folder-picker").value = "";
    await refresh();
  }
}
for (const id of ["file-picker", "folder-picker"]) $(id).onchange = event => action(() => importFiles(Array.from(event.target.files)));

async function entryFiles(entry) {
  if (entry.isFile) return [await new Promise((resolve, reject) => entry.file(resolve, reject))];
  if (!entry.isDirectory) return [];
  const reader = entry.createReader(), files = [];
  while (true) {
    const entries = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!entries.length) return files;
    for (const child of entries) files.push(...await entryFiles(child));
  }
}
// Prevent the browser navigating away when a file misses the drop area.
for (const event of ["dragover", "drop"]) window.addEventListener(event, e => e.preventDefault());
$("drop-zone").addEventListener("dragover", event => { event.preventDefault(); $("drop-zone").classList.add("drag-over"); });
$("drop-zone").addEventListener("dragleave", () => $("drop-zone").classList.remove("drag-over"));
$("drop-zone").addEventListener("drop", event => {
  event.preventDefault(); $("drop-zone").classList.remove("drag-over");
  const entries = Array.from(event.dataTransfer.items || []).filter(item => item.kind === "file").map(item => item.webkitGetAsEntry?.());
  const fallback = Array.from(event.dataTransfer.files);
  action(async () => {
    const files = [];
    if (entries.length && entries.every(Boolean)) {
      for (const entry of entries) files.push(...await entryFiles(entry));
    } else files.push(...fallback);
    await importFiles(files);
  });
});

function readPending() {
  try {
    const value = JSON.parse(sessionStorage.getItem("sopmaker-pending-run") || "null");
    return value && /^[a-f0-9]{32}$/.test(value.runId) && /^[a-f0-9]{32}$/.test(value.instanceId) && Number.isFinite(value.clickedAt) ? value : null;
  } catch { return null; }
}
let pendingRun = readPending(), startInFlight = null, cancelInFlight = false, connectionLost = false;
let activeView = "notes", refreshId = 0, appliedRefresh = 0, polling = false;
function keepPending(value) {
  pendingRun = value;
  if (!value && $("message").dataset.topic === "start") $("message").hidden = true;
  try { value ? sessionStorage.setItem("sopmaker-pending-run", JSON.stringify(value)) : sessionStorage.removeItem("sopmaker-pending-run"); } catch { /* Current-window feedback still works if storage is unavailable. */ }
}
function locked() { return !!pendingRun || !!state?.progress.running; }
function modelName(id) { return state?.model_options.find(option => option.id === id)?.label || "Custom model"; }
function setText(id, text) { if ($(id).textContent !== text) $(id).textContent = text; }
function elapsed(start, end = Date.now()) {
  const seconds = Math.max(0, Math.floor((end - Date.parse(start)) / 1000));
  return Number.isFinite(seconds) ? seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s` : "—";
}
function updateTiming() {
  if (!state) return;
  const p = state.progress;
  let text = "";
  if (pendingRun) text = `Clicked ${new Date(pendingRun.clickedAt).toLocaleTimeString()} · ${elapsed(new Date(pendingRun.clickedAt).toISOString())} elapsed`;
  else if (p.started_at) {
    text = `Started ${new Date(p.started_at).toLocaleTimeString()} · ${elapsed(p.started_at, p.finished_at ? Date.parse(p.finished_at) : Date.now())} elapsed`;
    if (p.running && p.file_started_at) text += ` · current file ${elapsed(p.file_started_at)}`;
  }
  setText("run-timing", text); setText("status-timing", text);
}
function downloadReport(url) {
  const link = node("a"); link.href = url; link.download = ""; link.click();
}
function renderRun() {
  if (!state) return;
  const p = state.progress, uncertain = !!pendingRun, busy = locked();
  const newFiles = state.assets.filter(a => a.state === "imported");
  const showNextBatch = !p.running && !uncertain && newFiles.length > 0;
  const phases = {
    starting: ["Starting processing", "Sopmaker accepted the run. Preparing to process the captured batch."],
    preparing: ["Preparing media", "Reading capture metadata and preparing images or audio on this device."],
    transcribing: ["Waiting for transcription", "An audio request is in progress. Low PC activity can be normal while waiting for OpenAI."],
    drafting: ["Waiting for draft observations", "A notes request is in progress. Low PC activity can be normal while waiting for OpenAI."],
    saving: ["Saving observations", "Saving the draft notes and their source evidence to this notebook."],
    packaging: ["Preparing the report ZIP", "Processing is complete. Packaging the Word draft and linked evidence; the download is not ready yet."],
    closing: ["Finishing processing", "Closing the API client before publishing the result. Saved notes are already available."]
  };
  let title, detail, kind = "idle";
  if (uncertain) {
    const restarted = p.instance_id !== pendingRun.instanceId;
    title = restarted ? "App restarted before the request was confirmed" : Date.now() - pendingRun.clickedAt < 30000 && startInFlight === pendingRun.runId ? "Starting…" : "Still waiting for confirmation";
    detail = restarted ? "Previous run status cannot be recovered in this app session. Review saved notes and source files before explicitly retrying." : "The request may have started. Nothing has been resubmitted. Check status; keep Sopmaker open.";
    kind = "busy";
  } else if (connectionLost) {
    title = "Cannot confirm the current status";
    detail = "The local app did not respond. No work was resubmitted. Keep Sopmaker open and check status; saved notes remain in the notebook.";
    kind = "busy";
  } else if (p.running) {
    [title, detail] = phases[p.stage] || phases.starting;
    if (cancelInFlight || p.cancel_requested) {
      title = p.cancel_requested ? "Cancel requested" : "Requesting cancellation…";
      detail = "The current stage may finish and an API request may still be billed. No new stage starts after cancellation is applied. Saved notes are kept.";
    }
    if (p.current && !["packaging", "closing"].includes(p.stage)) title += ` · file ${p.asset_ids.indexOf(p.current_id) + 1} of ${p.total} · ${p.current}`;
    detail += ` ${p.completed} of ${p.total} files completed; ${p.failed} failed; ${p.notes} new observation(s) saved.`;
    kind = "busy";
  } else if (p.state === "completed") {
    title = p.report_url ? "Your draft report is ready" : "Notes are ready";
    detail = p.report_url ? `${p.report_notes} saved observation(s) in this batch's draft report. Review them before use.` : `${p.completed} file(s) completed; ${p.notes} new observation(s) saved. Select saved notes in Review notes to export them together.`;
    kind = "ready";
  } else if (p.state === "failed") {
    title = "Processing did not complete";
    detail = p.report_error || "A file failed. Review Add files and retry explicitly, then export selected completed notes.";
    detail += ` ${p.completed} of ${p.total} files completed; ${p.notes} new observation(s) saved. No full report was published.`;
    kind = "failed";
  } else if (p.state === "cancelled") {
    title = "Processing cancelled";
    detail = `${p.completed} of ${p.total} files completed; ${p.notes} new observation(s) saved and kept. No report was published. Review files and retry explicitly.`;
    if (p.cleanup_error) detail += " Cleanup also failed; check diagnostics.";
    kind = "cancelled";
  } else if (p.state === "empty") {
    title = "No observations generated";
    detail = "Processing finished with no observations. Review the sources; no report was generated. This does not establish that the inspection passed.";
    kind = "ready";
  } else if (!state.credentials.configured) {
    title = "Add your OpenAI API key to generate reports"; detail = "Adding files only copies them to this device and works without a key.";
  } else {
    title = newFiles.length ? `${newFiles.length} new file(s) ready to process` : "Ready. Nothing is running.";
    detail = newFiles.length ? "Generate a draft report when you are ready. Nothing is processing yet." : "Add inspection fragments to begin, or review saved notes.";
  }
  const resultTitle = title, resultDetail = detail, resultKind = kind;
  if (showNextBatch && !connectionLost && p.state === "completed") {
    title = `${newFiles.length} new file(s) ready to process`;
    detail = "These new files have not been processed. The previous run's result remains available on Generate report.";
    kind = "idle";
  }
  $("status-strip").className = `status-strip ${kind}`;
  setText("worker-status", title); setText("status-detail", detail);
  setText("status-icon", kind === "ready" ? "✓" : kind === "failed" ? "!" : kind === "cancelled" ? "‖" : "○");
  $("status-progress").hidden = kind !== "busy";
  $("run-panel").hidden = !busy && !connectionLost;
  setText("run-title", title); setText("run-detail", detail);
  $("cancel").hidden = !p.running || uncertain;
  $("cancel").disabled = p.cancel_requested || cancelInFlight;
  $("status-cancel").hidden = !p.running || uncertain || activeView === "generate";
  $("status-cancel").disabled = p.cancel_requested || cancelInFlight;
  $("check-status").hidden = !uncertain && !connectionLost;
  $("check-status").textContent = uncertain && p.instance_id !== pendingRun.instanceId ? "Review current app session" : "Check status now";
  $("generate-report").disabled = busy || connectionLost || importing || !newFiles.length || !state.credentials.configured;
  document.querySelectorAll("#files-list .text-button").forEach(button => { button.disabled = busy || connectionLost || importing; });
  $("generate-report").classList.toggle("inline-busy", busy);
  $("generate-report").setAttribute("aria-busy", String(busy));
  $("generate-report").textContent = busy ? uncertain ? "Starting…" : "Working…" : "Generate draft report";
  setText("generate-hint", busy ? "This run is locked against repeat submissions. You can review saved notes while waiting." : !state.credentials.configured ? "Add your API key in Setup before generating." : newFiles.length ? `One click starts work on ${newFiles.length} new file(s).` : "Add new files, or explicitly retry individual files in Add files.");
  setText("batch-headline", p.running || uncertain ? `Current batch: ${uncertain ? pendingRun.assetIds?.length || newFiles.length : p.total} file(s)` : `${newFiles.length} new file(s) to process`);
  setText("model-summary", `Notes model for future processing: ${modelName(state.settings.vision_model)}.`);
  setText("run-badge", busy ? "Working" : newFiles.length ? `${newFiles.length} new` : p.report_url ? "Ready" : "");
  const result = $("report-result");
  result.hidden = busy || connectionLost || !["completed", "failed", "cancelled", "empty"].includes(p.state);
  result.className = `panel result-${resultKind}`; result.replaceChildren();
  if (!result.hidden) {
    result.append(node("h2", showNextBatch ? `Previous run: ${resultTitle}` : resultTitle), node("p", resultDetail));
    if (p.model) result.append(node("p", `Requested notes model for this run: ${modelName(p.model)}. Actual generation models are recorded with each note when available.`, "small"));
    const actions = node("div", undefined, "result-actions");
    if (p.report_url) {
      const download = node("a", "Download draft report", "primary"); download.href = p.report_url; download.download = ""; actions.append(download);
    }
    const review = node("button", "Review saved notes", "secondary"); review.onclick = () => view("notes"); actions.append(review); result.append(actions);
    if (p.report_url) result.append(node("p", "Next: extract the ZIP, then open notes.docx in Word or index.html in your browser. This is a snapshot: editing Word does not edit this notebook, and later saved corrections need a new export.", "snapshot-help"));
  }
  const statusAction = $("status-action"); statusAction.hidden = false;
  statusAction.textContent = !busy && !connectionLost && !showNextBatch && p.report_url ? "Download draft report" : busy || connectionLost ? "Details" : !state.credentials.configured ? "Open Setup" : "Generate report";
  statusAction.onclick = () => !busy && !connectionLost && !showNextBatch && p.report_url ? downloadReport(p.report_url) : view(!busy && !connectionLost && !state.credentials.configured ? "setup" : "generate");
  const list = $("batch-files"); list.replaceChildren();
  const ids = uncertain ? pendingRun.assetIds || newFiles.map(a => a.id) : showNextBatch || !p.run_id ? newFiles.map(a => a.id) : p.asset_ids;
  for (const id of ids || []) {
    const asset = state.assets.find(a => a.id === id); if (!asset) continue;
    const row = node("div", undefined, "file-row"); const info = node("div"); info.append(node("div", asset.name, "file-name"), node("div", captureLabel(asset), "file-state"));
    const outcome = showNextBatch || uncertain ? null : p.files?.[id];
    const label = showNextBatch || uncertain ? "New" : outcome ? outcome.state === "ready" ? `✓ Completed · ${outcome.new_notes} new observation(s)` : outcome.state === "error" ? "! Failed · see Add files" : "Stopped · retry explicitly" : p.current_id === id && p.running ? "Working" : p.run_id ? p.running ? "Queued" : "Not completed" : "New";
    row.append(info, node("span", label, "batch-row-status")); list.append(row);
  }
  updateTiming();
}
async function refresh() {
  const requestId = ++refreshId;
  const query = new URLSearchParams({q: $("search").value});
  if ($("show-archived").checked) query.set("archived", "1");
  let next;
  try { next = await api("/api/state?" + query); }
  catch (error) { connectionLost = true; renderRun(); throw error; }
  if (requestId < appliedRefresh) return;
  if (state?.progress.instance_id === next.progress.instance_id && state.progress.sequence > next.progress.sequence) return;
  appliedRefresh = requestId; state = next; connectionLost = false;
  if (pendingRun && pendingRun.instanceId === state.progress.instance_id) {
    const pendingId = pendingRun.runId;
    const receipt = state.progress.run_id === pendingId ? state.progress : (await api(`/api/runs/${pendingId}`)).run;
    if (pendingRun?.runId === pendingId && receipt?.run_id === pendingId && receipt.instance_id === pendingRun.instanceId) keepPending(null);
  }
  setText("workspace-name", state.settings.name); setText("note-count", String(state.observations.length)); setText("file-count", String(state.assets.length));
  $("key-status").textContent = state.credentials.configured ? `Key ready · ${state.credentials.origin.replaceAll("_", " ")}` : "No key configured";
  $("welcome").hidden = !!state.assets.length || state.credentials.configured;
  if (!$("settings-form").dataset.loaded) {
    for (const [key, value] of Object.entries(state.settings)) if ($("settings-form").elements[key]) $("settings-form").elements[key].value = value;
    const model = state.settings.vision_model;
    if (!state.model_options.some(option => option.id === model)) { $("vision-model-choice").value = "custom"; $("custom-model").value = model; }
    updateModelControl(); $("settings-form").dataset.loaded = "yes";
  }
  setText("diagnostic-path", state.diagnostics.path ? `Local log: ${state.diagnostics.path}` : "Persistent logging is not enabled in this session.");
  $("diagnostic-warning").hidden = !state.diagnostics.write_failed;
  const focusedControl = document.activeElement?.dataset.focusKey;
  renderNotes(); renderFiles(); renderRun();
  if (focusedControl) document.querySelector(`[data-focus-key="${CSS.escape(focusedControl)}"]`)?.focus({preventScroll: true});
}
function beginRun(assetIds = null) {
  if (!state) return;
  if (locked() || connectionLost || importing) return;
  if (!state.credentials.configured) { view("setup"); message("Add your OpenAI API key before processing.", true); return; }
  const ids = assetIds || state.assets.filter(a => a.state === "imported").map(a => a.id);
  if (!ids.length) { message("Import a new file first, or explicitly retry a file in Add files.", true); return; }
  const runId = crypto.randomUUID().replaceAll("-", "");
  keepPending({runId, instanceId: state.progress.instance_id, clickedAt: Date.now(), assetIds: ids});
  startInFlight = runId; $("message").hidden = true; view("generate"); renderRun();
  action(async () => {
    try {
      const result = await api("/api/process", {asset_ids: ids, generate_report: assetIds === null, run_id: runId, intent: assetIds === null ? "new" : "retry"});
      if (pendingRun?.runId === runId && result.run.run_id === runId && result.run.instance_id === pendingRun.instanceId) keepPending(null);
      if (result.run.instance_id === state.progress.instance_id && result.run.sequence >= state.progress.sequence) state.progress = result.run;
      connectionLost = false;
      await refresh();
    } catch (error) {
      if (error.status >= 400 && error.status < 500) {
        if (pendingRun?.runId === runId) keepPending(null);
        if (!pendingRun || pendingRun.runId === runId) message(error.message, true);
        try { await refresh(); } catch { /* Reconciliation remains read-only. */ }
      }
      else if (pendingRun?.runId === runId) { message("The start response was not confirmed. Check status; the request has not been resubmitted.", true, "start"); }
      else if (!pendingRun && state.progress.run_id === runId) message(error.message, true);
    } finally { if (startInFlight === runId) startInFlight = null; renderRun(); }
  });
}
async function cancelRun() {
  if (!state?.progress.running || cancelInFlight || state.progress.cancel_requested) return;
  cancelInFlight = true; renderRun();
  try { await api("/api/cancel", {run_id: state.progress.run_id}); await refresh(); }
  finally { cancelInFlight = false; renderRun(); }
}
function updateModelControl() {
  const custom = $("vision-model-choice").value === "custom";
  $("custom-model-label").hidden = !custom; $("custom-model").required = custom;
  if (custom) $("model-advanced").open = true;
}

function captureLabel(asset) {
  const time = asset.prepared?.capture_time;
  return time?.utc ? `${new Date(time.utc).toLocaleString()} · ${time.basis.replaceAll("_", " ")}` : "Capture time unknown";
}
function renderNotes() {
  const signature = JSON.stringify([state.observations.map(n => [n.id, n.revision, n.state, n.archived]), focused, [...selected], $("state-filter").value]);
  if (signature === lastNoteSignature) { selectionCount(); return; }
  lastNoteSignature = signature;
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
    checkbox.dataset.focusKey = `select-note:${note.id}`;
    checkbox.setAttribute("aria-label", `Select ${note.generated.title} for export`);
    checkbox.onchange = () => { checkbox.checked ? selected.add(note.id) : selected.delete(note.id); selectionCount(); };
    const open = node("button", undefined, "open-note");
    open.dataset.focusKey = `open-note:${note.id}`;
    open.append(node("span", note.generated.title, "note-title"), node("p", note.note.slice(0, 210), "note-preview"));
    const meta = node("div", undefined, "meta"); meta.append(node("span", note.state, `badge ${note.state}`), node("span", asset.name), node("span", note.archived ? "Previous draft" : captureLabel(asset))); open.append(meta);
    open.onclick = () => {
      if (dirty && !confirm("Discard unsaved changes to this note?")) return;
      focused = note.id; dirty = false; renderNotes(); renderDetail(note, asset);
      $("note-editor").focus({preventScroll: true});
      if (innerWidth <= 1200) $("detail").scrollIntoView({block: "start"});
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
  let editGeneration = 0, revisionToken = note.revision;
  const asset = boundAsset(note, currentAsset);
  const detail = $("detail"); detail.replaceChildren();
  const head = node("div", undefined, "detail-head"); head.append(node("span", note.state, `badge ${note.state}`), node("h2", note.generated.title)); detail.append(head);
  head.append(node("p", note.generation_model ? `Generated with ${note.generation_model}` : "Generation model not recorded for this note.", "small"));
  const body = node("div", undefined, "detail-body");
  const label = node("label", "Edit the note before using it in your report"); label.htmlFor = "note-editor"; body.append(label);
  const editor = node("textarea"); editor.id = "note-editor"; editor.rows = 4; editor.value = note.note; body.append(editor);
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
    const snapshot = {id: note.id, revision: revisionToken, note: editor.value, state: reviewed.checked ? "reviewed" : "draft", reviewer: reviewer.value};
    save.disabled = true;
    try {
      const result = await api("/api/edit", snapshot);
      // Keep this editor's token current without partially mutating the shared
      // observation used by cached list/card closures before refresh completes.
      revisionToken = result.revision;
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
  const evidence = node("details", undefined, "evidence-panel"); evidence.open = true;
  evidence.append(node("summary", "Original evidence and sampled frames")); renderEvidence(evidence, asset); body.append(evidence);
  for (const [heading, values] of [["Follow-up", note.generated.follow_ups], ["Open questions", note.generated.open_questions]]) {
    if (values.length) { body.append(node("h3", heading)); const list = node("ul"); values.forEach(v => list.append(node("li", v))); body.append(list); }
  }
  body.append(node("h3", "Original source-linked claims"), node("p", "These are the original AI claims and citations. Your edited note is saved separately above.", "small"));
  for (const [basis, title] of [["visible_detail", "Seen in the image"], ["inspector_narration", "What the inspector said"]]) {
    const claims = note.generated.claims.filter(claim => claim.basis === basis);
    if (!claims.length) continue;
    const group = node("section", undefined, "claims-group"); group.append(node("h3", title));
    for (const claim of claims) {
      const item = node("div", claim.text, "claim"); item.append(node("small", claim.evidence.map(e => e.source_id).join(", ")));
      for (const reference of claim.evidence) if (reference.quote) item.append(node("p", `“${reference.quote}”`)); group.append(item);
    }
    body.append(group);
  }
  renderTranscript(body, asset);
  const history = node("button", "View previous saved revisions", "text-button"); history.onclick = () => action(async () => {
    const revisions = await api(`/api/history/${note.id}`); const panel = node("div");
    revisions.forEach(r => panel.append(node("h3", `Revision ${r.revision} · ${r.state}`), node("p", r.note, "transcript")));
    if (!revisions.length) panel.append(node("p", "No previous saved revisions.")); history.replaceWith(panel);
  }); body.append(node("br"), history); detail.append(body);
}
function renderFiles() {
  const signature = JSON.stringify([state.assets.map(a => [a.id, a.state, a.error, a.prepared?.revision_id, a.transcript?.fingerprint]), $("file-search").value, locked(), connectionLost, importing]);
  if (signature === lastFileSignature) return;
  lastFileSignature = signature;
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
    open.dataset.focusKey = `open-source:${asset.id}`;
    open.onclick = () => {
      const body = $("source-detail"); body.hidden = false; body.replaceChildren(node("h2", asset.name), node("p", `Processing state: ${asset.state}`, "small"));
      if (asset.error) body.append(node("p", asset.error, "file-error"));
      renderEvidence(body, asset); renderTranscript(body, asset); body.scrollIntoView({block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
      body.tabIndex = -1; body.focus({preventScroll: true});
    }; row.append(open);
    if (["error", "interrupted", "ready"].includes(asset.state)) {
      const retry = node("button", asset.state === "ready" ? "Update draft" : "Retry explicitly", "text-button"); retry.disabled = locked() || connectionLost;
      retry.dataset.focusKey = `retry-source:${asset.id}`;
      retry.onclick = () => beginRun([asset.id]); row.append(retry);
    }
    list.append(row);
  }
}
$("generate-report").onclick = () => beginRun();
$("cancel").onclick = $("status-cancel").onclick = () => action(cancelRun);
$("check-status").onclick = () => action(async () => {
  if (pendingRun && state.progress.instance_id !== pendingRun.instanceId) {
    keepPending(null); message("The app session changed. Review saved notes and interrupted files before explicitly retrying.");
  }
  await refresh();
});
$("vision-model-choice").onchange = updateModelControl;
$("settings-form").onsubmit = event => { event.preventDefault(); action(async () => {
  const values = Object.fromEntries(new FormData(event.target));
  if (values.vision_model === "custom") values.vision_model = $("custom-model").value.trim();
  await api("/api/settings", values); await refresh(); message("Notebook details saved. Model changes apply to future processing only.");
}); };
$("key-form").onsubmit = event => { event.preventDefault(); action(async () => {
  const key = $("api-key").value; $("api-key").value = "";
  await api("/api/key", {key, remember: $("remember-key").checked}); await refresh(); message("Key configured. It will be checked when you explicitly process evidence.");
}); };
for (const [id, path] of [["load-key", "load"], ["clear-key", "clear"], ["forget-key", "forget"]]) $(id).onclick = () => action(async () => { await api(`/api/key/${path}`, {}); await refresh(); message("Credential setting updated."); });
$("export").onclick = () => action(async () => {
  if (dirty) { message("Save or discard your unsaved note changes before exporting. Exports use saved revisions.", true); return; }
  const result = await api("/api/export", {ids: [...selected], include_drafts: $("include-drafts").checked});
  const download = node("a"); download.href = result.url; download.download = ""; download.click(); message("Export ready: editable Word notes, CSV, JSON, and an evidence index with originals.");
});
$("search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => action(refresh), 250); };
$("state-filter").onchange = () => renderNotes(); $("show-archived").onchange = () => action(refresh);
$("file-search").oninput = () => renderFiles();
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
action(async () => { await api("/api/session", {}); await refresh(); if (!state.credentials.configured && !state.assets.length) view("setup"); });
setInterval(async () => {
  if (polling || !(locked() || connectionLost)) return;
  polling = true;
  try { await refresh(); } catch { /* The lifecycle keeps the last confirmed state visible. */ }
  finally { polling = false; }
}, 2000);
setInterval(updateTiming, 1000);
