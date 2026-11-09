// Escape untrusted text before it is placed into innerHTML / template strings.
function escapeHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/**
 * Nalarka Motion Graphics Studio v8.1 - Web Client Application
 */

// Toast notifications
function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Modal management
function openModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) el.classList.add("active");
}

function closeModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) {
    el.classList.remove("active");
    // Pause any playing videos
    const videos = el.querySelectorAll("video");
    videos.forEach(v => v.pause());
  }
}

// ---------------------------------------------------------------------------------------------
// Log rendering shared by the page console and the activity drawer
// ---------------------------------------------------------------------------------------------
function logClass(text) {
  const t = String(text || "");
  if (/\[JOB ERROR\]|\bERROR\b|\[FAIL\]|Traceback|rejected|failed/i.test(t)) return "err";
  if (/WARN|\[CODE ERROR\]|\[CODE REJECTED\]/.test(t)) return "warn";
  if (/\[PASS\]|\[JOB COMPLETE\]|\[KEEP\]|Completed/.test(t)) return "ok";
  if (/Replayed saved AI|\[RESUME\]/.test(t)) return "replay";
  if (/^\[(JOB START|STAGE)|^={5,}/.test(t)) return "stage";
  return "";
}

function fmtClock(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d)) return String(ts).slice(11, 19);
  return d.toLocaleTimeString([], { hour12: false });
}

// Appends one line to a log element; keeps the view pinned to the bottom unless the user scrolled up.
function appendLogLine(el, message, ts) {
  if (!el || message === undefined || message === null) return;
  const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  String(message).split("\n").forEach((line) => {
    const row = document.createElement("span");
    row.className = "log-line " + logClass(line);
    if (ts) {
      const t = document.createElement("span");
      t.className = "ts";
      t.textContent = fmtClock(ts);
      row.appendChild(t);
    }
    row.appendChild(document.createTextNode(line));
    el.appendChild(row);
  });
  if (stick) el.scrollTop = el.scrollHeight;
}

function setLogText(el, text) {
  if (!el) return;
  el.textContent = "";
  if (text) appendLogLine(el, text);
}

// SSE Job Connection. The server sends NAMED events (log / progress / result / done / error),
// so each one needs its own listener. The stream replays the job from the start, so attaching
// late or after a page reload still shows the whole log.
function connectJobEvents(jobId, options = {}) {
  const {
    consoleId = "live-console",
    progressId = "job-progress",
    statusId = "job-status",
    onDone = null,
    onError = null,
    onLine = null,
  } = options;

  const consoleEl = document.getElementById(consoleId);
  const progressEl = document.getElementById(progressId);
  const statusEl = document.getElementById(statusId);

  if (consoleEl) setLogText(consoleEl, "");
  if (progressEl) progressEl.value = 0;
  if (statusEl) {
    statusEl.textContent = "Running";
    statusEl.className = "badge badge-blue";
  }
  window.CURRENT_JOB_ID = jobId;

  const evtSource = new EventSource(`/api/jobs/${jobId}/events`);
  let finished = false;

  const parse = (e) => { try { return e.data ? JSON.parse(e.data) : {}; } catch (_) { return { message: e.data }; } };

  evtSource.addEventListener("log", (e) => {
    const d = parse(e);
    appendLogLine(consoleEl, d.message, d.ts);
    if (onLine) onLine(d);
  });

  evtSource.addEventListener("progress", (e) => {
    const d = parse(e);
    if (progressEl && typeof d.pct === "number") progressEl.value = Math.max(0, Math.min(100, d.pct));
    if (statusEl && d.status) statusEl.textContent = d.status;
    if (typeof window.onPipelineProgress === "function") window.onPipelineProgress(d);
  });

  evtSource.addEventListener("done", (e) => {
    finished = true;
    evtSource.close();
    const data = parse(e);
    window.dispatchEvent(new CustomEvent("nalarka:job-finished", { detail: { jobId, ...data } }));

    if (data.status === "failed" || data.status === "canceled" || data.error) {
      if (statusEl) {
        statusEl.textContent = data.status === "canceled" ? "Canceled" : "Failed";
        statusEl.className = "badge badge-red";
      }
      showToast(`Job ${data.status || "failed"}: ${data.error || "Unknown error"}`, "error");
      if (onError) onError(data);
      return;
    }
    if (progressEl) progressEl.value = 100;
    if (statusEl) {
      statusEl.textContent = "Completed";
      statusEl.className = "badge badge-green";
    }
    showToast("Job completed.", "success");
    if (onDone) onDone(data);
  });

  // Server-side "error" events carry data; plain connection errors do not (the browser retries by itself).
  evtSource.addEventListener("error", (e) => {
    if (finished) return;
    if (e && e.data) {
      finished = true;
      evtSource.close();
      const d = parse(e);
      if (statusEl) { statusEl.textContent = "Failed"; statusEl.className = "badge badge-red"; }
      showToast(`Job error: ${d.message || d.error || "unknown"}`, "error");
      if (onError) onError(d);
    } else if (evtSource.readyState === EventSource.CLOSED) {
      appendLogLine(consoleEl, "[UI] Lost the live connection. The job keeps running; its log is saved - reload to reattach.");
    }
  });

  return evtSource;
}

// Loads the saved log of a job into a console element (works for finished and running jobs).
async function loadSavedLog(jobId, consoleEl, progressEl, statusEl) {
  const res = await fetch(`/api/jobs/${jobId}/log?limit=5000`);
  if (!res.ok) return null;
  const d = await res.json();
  if (consoleEl) {
    setLogText(consoleEl, "");
    d.events.filter((e) => e.kind === "log").forEach((e) => appendLogLine(consoleEl, e.message, e.ts));
  }
  if (progressEl) progressEl.value = d.progress || 0;
  if (statusEl) {
    const map = { succeeded: ["Completed", "badge-green"], failed: ["Failed", "badge-red"], canceled: ["Canceled", "badge-red"], running: ["Running", "badge-blue"], queued: ["Queued", "badge-orange"] };
    const [txt, cls] = map[d.status] || [d.status, ""];
    statusEl.textContent = txt;
    statusEl.className = "badge " + cls;
  }
  return d;
}

// Live Script Estimator (debounced)
let estimateDebounceTimer = null;
function updateScriptEstimates() {
  const textarea = document.getElementById("script-editor");
  const maxDurInput = document.getElementById("max-duration-input");
  if (!textarea || !maxDurInput) return;

  const rawText = textarea.value;
  const maxDur = parseInt(maxDurInput.value, 10) || 60;

  clearTimeout(estimateDebounceTimer);
  estimateDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch("/api/scripts/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_text: rawText, max_duration: maxDur }),
      });
      if (!res.ok) return;
      const data = await res.json();

      const wcEl = document.getElementById("script-word-count");
      const estVoEl = document.getElementById("script-est-vo");
      const estVidEl = document.getElementById("script-est-vid");
      const budgetEl = document.getElementById("script-budget-range");
      const overCapEl = document.getElementById("script-over-cap");

      if (wcEl) wcEl.textContent = `${data.word_count} words`;
      if (estVoEl) estVoEl.textContent = `~${data.est_voice_seconds}s voice`;
      if (estVidEl) estVidEl.textContent = `~${data.est_video_seconds}s video`;
      if (budgetEl) budgetEl.textContent = `Budget: ${data.word_budget_lo}-${data.word_budget_hi} w`;

      if (overCapEl) {
        if (data.over_cap) {
          overCapEl.style.display = "inline-block";
        } else {
          overCapEl.style.display = "none";
        }
      }
    } catch (e) {
      console.error("Estimate error:", e);
    }
  }, 300);
}

// Duration Hint updates
async function updateDurationHint() {
  const maxDurInput = document.getElementById("max-duration-input");
  const hintEl = document.getElementById("duration-hint-text");
  if (!maxDurInput || !hintEl) return;

  const d = Math.max(15, Math.min(900, parseInt(maxDurInput.value, 10) || 60));
  try {
    const res = await fetch(`/api/duration-hint?d=${d}`);
    if (!res.ok) return;
    const data = await res.json();
    hintEl.textContent = `Budget: ${data.word_budget_lo}–${data.word_budget_hi} words | ${data.recommended_sections} sections | ~${data.beats_lo}–${data.beats_hi} beats (${data.mode_label})`;
  } catch (e) {
    console.error("Duration hint error:", e);
  }
}

// Clipboard copy
async function copyCleanText(scriptId) {
  try {
    let cleanText = "";
    if (scriptId) {
      const res = await fetch(`/api/scripts/${scriptId}/clean-text`);
      if (res.ok) {
        const d = await res.json();
        cleanText = d.clean_text;
      }
    } else {
      const textarea = document.getElementById("script-editor");
      if (textarea) {
        // Strip tags in browser
        cleanText = textarea.value.replace(/\[(?:Hook|Inti\s*\d+|Penutup)\]/gi, "").trim();
      }
    }
    await navigator.clipboard.writeText(cleanText);
    showToast("Clean text copied to clipboard!", "success");
  } catch (e) {
    showToast("Failed to copy clean text: " + e.message, "error");
  }
}

// Video Player Modal opener
function playVideoModal(renderId, title = "Rendered Video") {
  const modal = document.getElementById("video-modal");
  const source = document.getElementById("video-modal-source");
  const video = document.getElementById("video-modal-player");
  const titleEl = document.getElementById("video-modal-title");
  const dlLink = document.getElementById("video-modal-download");

  if (!modal || !source || !video) return;

  if (titleEl) titleEl.textContent = title;
  source.src = `/api/renders/${renderId}/video`;
  if (dlLink) dlLink.href = `/api/renders/${renderId}/download`;
  video.load();
  openModal("video-modal");
  video.play().catch(() => {});
}

// Verification Details Modal opener
async function showVerificationModal(renderId) {
  const modal = document.getElementById("verification-modal");
  const content = document.getElementById("verification-modal-content");
  if (!modal || !content) return;

  content.innerHTML = "<p>Loading verification suite results...</p>";
  openModal("verification-modal");

  try {
    const res = await fetch(`/api/renders/${renderId}/verification`);
    if (!res.ok) {
      content.innerHTML = "<p class='text-muted'>No verification results found for this render.</p>";
      return;
    }
    const data = await res.json();
    const checks = Array.isArray(data.results) ? data.results : [];
    const rows = checks.map((c, i) => `<tr>
      <td>${escapeHtml(c.name || "Check " + (i + 1))}</td>
      <td>${c.passed ? '<span class="badge badge-green">Pass</span>' : '<span class="badge badge-red">Fail</span>'}</td>
      <td style="font-family: var(--font-mono); font-size: .8rem;">${escapeHtml(c.detail || "-")}</td></tr>`).join("");
    const ok = data.passed === data.total;
    content.innerHTML = `
      <p style="margin-bottom: .8rem;"><span class="badge ${ok ? "badge-green" : "badge-red"}">${data.passed}/${data.total} passed</span>
        ${data.duration_sec ? `<span class="text-muted" style="margin-left:.5rem;">${Number(data.duration_sec).toFixed(2)} s · ${data.frames ?? "?"} frames</span>` : ""}</p>
      ${rows ? `<div class="table-container"><table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead><tbody>${rows}</tbody></table></div>`
             : `<p class="text-muted">This verification was saved before per-check details were recorded. Run Verify again to see them.</p>`}
      ${data.contact_sheet_path ? `<p style="margin-top:.8rem;"><button class="btn btn-secondary btn-sm" onclick="showImagePreview('/api/renders/preview-image?path=' + encodeURIComponent(${JSON.stringify(data.contact_sheet_path)}), 'Contact sheet')">View contact sheet</button></p>` : ""}
    `;
  } catch (e) {
    content.innerHTML = `<p class="text-danger">Error: ${escapeHtml(e.message)}</p>`;
  }
}

// Image Preview Modal opener
function showImagePreview(src, title = "Beat Preview Contact Sheet") {
  const modal = document.getElementById("preview-image-modal");
  const img = document.getElementById("preview-modal-img");
  const titleEl = document.getElementById("preview-modal-title");
  if (!modal || !img) return;
  if (titleEl) titleEl.textContent = title;
  img.src = src;
  openModal("preview-image-modal");
}


// ---------------------------------------------------------------------------------------------
// Videos (registered in the database, shown wherever renders are listed)
// ---------------------------------------------------------------------------------------------
function fmtBytes(n) {
  if (!n) return "-";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtTimecode(sec) {
  if (!sec && sec !== 0) return "";
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function renderStateBadge(r) {
  if (r.status === "running" || r.status === "queued") return '<span class="badge badge-blue">Rendering</span>';
  if (r.status === "canceled") return '<span class="badge">Canceled</span>';
  if (r.verification_total) {
    const ok = r.verification_passed === r.verification_total;
    return `<button class="badge ${ok ? "badge-green" : "badge-red"}" style="border:0" onclick="showVerificationModal(${r.id})">${r.verification_passed}/${r.verification_total} checks</button>`;
  }
  if (r.status === "failed") return '<span class="badge badge-red">Failed</span>';
  return '<span class="badge">Not verified</span>';
}

function videoItemHtml(r, opts = {}) {
  const landscape = r.width && r.height && r.width > r.height;
  const has = r.video_available;
  const thumb = has
    ? `<button class="video-thumb ${landscape ? "landscape" : ""}" onclick="playVideoModal(${r.id}, ${JSON.stringify("Render #" + r.id).replace(/"/g, "&quot;")})" aria-label="Play render ${r.id}">
         ${r.poster_path ? `<img src="/api/renders/${r.id}/poster" alt="" loading="lazy" onerror="this.remove()">` : ""}
         <span class="play"></span>${r.total_dur ? `<span class="tc">${fmtTimecode(r.total_dur)}</span>` : ""}</button>`
    : `<div class="video-thumb empty ${landscape ? "landscape" : ""}"></div>`;
  const facts = [
    r.width && r.height ? `${r.width}×${r.height}` : null,
    r.total_dur ? `${r.total_dur}s` : null,
    r.file_size ? fmtBytes(r.file_size) : null,
    r.sha256 ? `sha ${r.sha256.slice(0, 8)}` : null,
  ].filter(Boolean).join(" · ");
  const when = (r.finished_at || r.created_at || "").slice(0, 16).replace("T", " ");
  const actions = has
    ? `<button class="btn btn-secondary btn-sm" onclick="playVideoModal(${r.id}, 'Render #${r.id}')">Play</button>
       <a class="btn btn-secondary btn-sm" href="/api/renders/${r.id}/download" download>Download</a>
       <button class="btn btn-secondary btn-sm" onclick="verifyRender(${r.id})">Verify</button>`
    : (r.error ? `<span class="text-danger" style="font-size:.8rem;">${escapeHtml(String(r.error).slice(0, 140))}</span>` : "");
  const title = opts.showTopic && r.topic_title ? `<a href="/topics/${r.topic_id}">${escapeHtml(r.topic_title)}</a>` : `Render #${r.id}`;
  return { thumb, title, facts, when, actions, badge: renderStateBadge(r), keptNote: (r.status === "failed" && has) ? "Verification failed, video kept." : "" };
}

async function loadTopicVideos(topicId, containerId = "video-list") {
  const box = document.getElementById(containerId);
  if (!box) return;
  try {
    const res = await fetch(`/api/renders?topic_id=${topicId}`);
    if (!res.ok) throw new Error("Could not load videos");
    const rows = await res.json();
    if (!rows.length) {
      box.innerHTML = `<div class="empty-state"><b>No video yet</b>Generate &amp; Render MP4 and the finished video appears here, saved in the database.</div>`;
      return;
    }
    box.innerHTML = rows.slice(0, 6).map((r) => {
      const v = videoItemHtml(r);
      return `<div class="video-item">${v.thumb}<div class="video-meta">
        <h4>${v.title} ${v.badge}</h4>
        <div class="video-facts">${escapeHtml(v.facts)}${v.facts && v.when ? "<br>" : ""}${escapeHtml(v.when)}</div>
        ${v.keptNote ? `<div class="text-muted" style="font-size:.8rem;margin-top:.2rem;">${v.keptNote}</div>` : ""}
        <div class="video-actions">${v.actions}</div></div></div>`;
    }).join("");
    const latest = rows.find((r) => r.video_available);
    if (latest) window.LATEST_RENDER_ID = latest.id;
  } catch (e) {
    box.innerHTML = `<p class="text-danger">${escapeHtml(e.message)}</p>`;
  }
}

async function verifyRender(renderId) {
  try {
    const res = await fetch(`/api/renders/${renderId}/verify`, { method: "POST" });
    if (!res.ok) throw new Error("Could not start verification");
    const data = await res.json();
    showToast("Verification started.", "success");
    toggleActivity(true, data.job_id);
  } catch (e) { showToast(e.message, "error"); }
}

// ---------------------------------------------------------------------------------------------
// AI request ledger (what was asked, what was paid for, what a resume reuses)
// ---------------------------------------------------------------------------------------------
function aiSummaryHtml(s) {
  const tok = (c) => Math.round((c || 0) / 4).toLocaleString();
  return `<div class="ai-summary">
    <div><b>${s.paid_calls || 0}</b><span>paid requests<br>≈ ${tok((s.paid_req_chars || 0) + (s.paid_resp_chars || 0))} tokens</span></div>
    <div class="saved"><b>${s.replayed_calls || 0}</b><span>reused from saved<br>≈ ${tok((s.saved_req_chars || 0) + (s.saved_resp_chars || 0))} tokens not re-bought</span></div>
    <div><b>${s.error_calls || 0}</b><span>failed requests</span></div></div>`;
}

async function loadTopicAiUsage(topicId, containerId = "ai-usage") {
  const box = document.getElementById(containerId);
  if (!box) return;
  try {
    const res = await fetch(`/api/llm-calls?topic_id=${topicId}&limit=1`);
    if (!res.ok) return;
    const d = await res.json();
    box.innerHTML = d.summary.total
      ? aiSummaryHtml(d.summary)
      : `<p class="text-muted" style="font-size:.88rem;">No AI requests yet. Every request is saved, so a failed run can continue without paying twice.</p>`;
  } catch (_) {}
}

function showTextModal(title, text) {
  const m = document.getElementById("verification-modal-content");
  if (!m) return;
  const modal = document.getElementById("verification-modal");
  modal.querySelector("h3").textContent = title;
  m.innerHTML = `<pre class="log-view" style="height:auto;max-height:60vh;white-space:pre-wrap;">${escapeHtml(text)}</pre>`;
  openModal("verification-modal");
}

async function viewAiCall(callId) {
  const res = await fetch(`/api/llm-calls/${callId}`);
  if (!res.ok) return;
  const c = await res.json();
  showTextModal(`AI request #${c.id} · ${c.tag || ""} · ${c.model || ""}`,
    `STATUS: ${c.status}${c.error ? "  ERROR: " + c.error : ""}\n\n--- SYSTEM ---\n${c.system_prompt || ""}\n\n--- PROMPT ---\n${c.user_prompt || ""}\n\n--- RESPONSE ---\n${c.response_text || "(none)"}`);
}

// ---------------------------------------------------------------------------------------------
// Activity drawer: all jobs, saved logs, AI requests, resume / cancel
// ---------------------------------------------------------------------------------------------
const KIND_LABEL = {
  generate_script: "Write script", generate_tts: "Voiceover", full_render: "Render video", reroll_beat: "Regenerate beat",
  preview_beat: "Preview beat", verify_mp4: "Verify MP4", scan_library: "Scan library", test_ai: "Test AI connection",
  fetch_openrouter_models: "Fetch models", fetch_el_voices: "Fetch voices",
};
const ACTIVITY = { open: false, selected: null, sse: null, timer: null, tab: "log", lastActive: new Set() };

function statusBadge(st) {
  const m = { succeeded: ["Done", "badge-green"], failed: ["Failed", "badge-red"], canceled: ["Canceled", "badge-red"], running: ["Running", "badge-blue"], queued: ["Queued", "badge-orange"] };
  const [t, c] = m[st] || [st, ""];
  return `<span class="badge ${c}">${t}</span>`;
}

function toggleActivity(force, jobId) {
  const open = typeof force === "boolean" ? force : !ACTIVITY.open;
  ACTIVITY.open = open;
  document.getElementById("activity-drawer")?.classList.toggle("open", open);
  document.getElementById("drawer-scrim")?.classList.toggle("open", open);
  document.getElementById("activity-drawer")?.setAttribute("aria-hidden", String(!open));
  document.getElementById("activity-toggle")?.setAttribute("aria-expanded", String(open));
  if (open) {
    if (jobId) ACTIVITY.selected = jobId;
    refreshJobList(true);
  } else { ACTIVITY.loadedFor = null; if (ACTIVITY.sse) { ACTIVITY.sse.close(); ACTIVITY.sse = null; } }
}

function showDrawerTab(tab) {
  ACTIVITY.tab = tab;
  document.getElementById("tab-log")?.classList.toggle("on", tab === "log");
  document.getElementById("tab-ai")?.classList.toggle("on", tab === "ai");
  const pl = document.getElementById("pane-log"), pa = document.getElementById("pane-ai");
  if (pl) pl.hidden = tab !== "log";
  if (pa) pa.hidden = tab !== "ai";
  if (tab === "ai" && ACTIVITY.selected) loadDrawerAi(ACTIVITY.selected);
}

async function refreshJobList(selectFirst = false) {
  const list = document.getElementById("job-list");
  if (!list) return;
  try {
    const res = await fetch("/api/jobs?limit=30");
    if (!res.ok) return;
    const jobs = await res.json();
    if (!jobs.length) return;
    if (selectFirst && !ACTIVITY.selected) ACTIVITY.selected = (jobs.find((j) => j.status === "running") || jobs[0]).id;
    list.innerHTML = jobs.map((j) => {
      const sel = j.id === ACTIVITY.selected;
      const resumable = j.status === "failed" || j.status === "canceled";
      const cancellable = j.status === "running" || j.status === "queued";
      return `<div class="job-row ${sel ? "sel" : ""}" onclick="selectJob('${j.id}')">
        <div style="min-width:0"><div class="jt">${escapeHtml(KIND_LABEL[j.kind] || j.kind)}${j.topic_id ? ` · topic ${j.topic_id}` : ""}</div>
          <div class="js">${escapeHtml((j.created_at || "").slice(5, 16))} · ${j.progress ?? 0}% ${j.status_text ? "· " + escapeHtml(String(j.status_text).slice(0, 40)) : ""}</div></div>
        <div style="display:flex;gap:.35rem;align-items:center;">${statusBadge(j.status)}
          ${resumable && j.kind === "full_render" && (j.params?.batch_beats || 6) > 1 ? `<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();resumeJob('${j.id}', ${Math.ceil((j.params?.batch_beats || 6) / 2)})" title="Resume, but ask the AI for ${Math.ceil((j.params?.batch_beats || 6) / 2)} beat(s) per request instead of ${j.params?.batch_beats || 6}. The storyboard plan is reused; code requests are asked again in smaller pieces.">Resume smaller</button>` : ""}
          ${resumable ? `<button class="btn btn-primary btn-sm" onclick="event.stopPropagation();resumeJob('${j.id}')" title="Re-run this job. Saved AI answers are reused, so you only pay for what is missing.">Resume</button>` : ""}
          ${cancellable ? `<button class="btn btn-danger btn-sm" onclick="event.stopPropagation();cancelJob('${j.id}')">Cancel</button>` : ""}</div></div>`;
    }).join("");
    if (ACTIVITY.selected && !ACTIVITY.loadedFor) selectJob(ACTIVITY.selected, true);
  } catch (_) {}
}

async function selectJob(jobId, keep) {
  ACTIVITY.selected = jobId;
  ACTIVITY.loadedFor = jobId;
  if (!keep) refreshJobList();
  const logEl = document.getElementById("drawer-log");
  if (ACTIVITY.sse) { ACTIVITY.sse.close(); ACTIVITY.sse = null; }
  const d = await loadSavedLog(jobId, logEl, null, null);
  if (!d) { setLogText(logEl, "Job not found."); return; }
  if (!logEl.childNodes.length) setLogText(logEl, "(no log lines saved for this job)");
  if (d.status === "running" || d.status === "queued") {
    // follow it live from where the saved log ended
    const es = new EventSource(`/api/jobs/${jobId}/events`, { withCredentials: false });
    let seen = d.events.filter((e) => e.kind === "log").length, n = 0;
    es.addEventListener("log", (e) => {
      n++;
      if (n <= seen) return;               // the stream replays from the start; skip what we already drew
      try { const x = JSON.parse(e.data); appendLogLine(logEl, x.message, x.ts); } catch (_) {}
    });
    es.addEventListener("done", () => { es.close(); refreshJobList(); });
    ACTIVITY.sse = es;
  } else if (d.error) {
    appendLogLine(logEl, "[JOB ERROR] " + d.error);
  }
  if (ACTIVITY.tab === "ai") loadDrawerAi(jobId);
}

async function loadDrawerAi(jobId) {
  const box = document.getElementById("drawer-ai");
  if (!box) return;
  const res = await fetch(`/api/jobs/${jobId}/llm-calls`);
  if (!res.ok) { box.innerHTML = ""; return; }
  const d = await res.json();
  if (!d.calls.length) { box.innerHTML = `<div class="empty-state"><b>No AI requests in this job</b>Script and vector-art requests are saved here, with their full text.</div>`; return; }
  box.innerHTML = aiSummaryHtml(d.summary) + `<div class="table-container"><table class="ai-table"><thead><tr><th>Step</th><th>Model</th><th>Result</th><th class="num">chars</th><th></th></tr></thead><tbody>` +
    d.calls.map((c) => {
      const st = c.status === "ok" ? (c.accepted ? '<span class="badge badge-green">paid · used</span>' : '<span class="badge badge-orange">paid · not usable</span>')
        : c.status === "replayed" ? '<span class="badge badge-teal">reused · free</span>' : '<span class="badge badge-red">error</span>';
      return `<tr><td>${escapeHtml(c.tag || "")}</td><td class="mono">${escapeHtml((c.model || "").split("/").pop())}</td><td>${st}</td>
        <td class="num">${(c.resp_chars || 0).toLocaleString()}</td><td><button class="btn btn-secondary btn-sm" onclick="viewAiCall(${c.id})">View</button></td></tr>`;
    }).join("") + `</tbody></table></div>`;
}

async function resumeJob(jobId, batchBeats) {
  try {
    const opts = { method: "POST" };
    if (batchBeats) {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify({ batch_beats: batchBeats });
    }
    const res = await fetch(`/api/jobs/${jobId}/resume`, opts);
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.detail || "Could not resume");
    showToast("Resumed. Saved AI answers are reused, so only missing steps are billed.", "success");
    ACTIVITY.selected = d.job_id; ACTIVITY.loadedFor = null;
    if (!ACTIVITY.open) toggleActivity(true, d.job_id); else refreshJobList();
    pollActiveJobs();
  } catch (e) { showToast(e.message, "error"); }
}

async function cancelJob(jobId) {
  try {
    await fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    showToast("Job canceled.", "success");
    refreshJobList();
  } catch (e) { showToast(e.message, "error"); }
}

// Background poll: counter on the rail, and a "jobs changed" signal when something finishes.
async function pollActiveJobs() {
  try {
    const res = await fetch("/api/jobs?status=active");
    if (!res.ok) return;
    const jobs = await res.json();
    const ids = new Set(jobs.map((j) => j.id));
    const cnt = document.getElementById("activity-count"), btn = document.getElementById("activity-toggle");
    if (cnt) cnt.textContent = jobs.length;
    if (btn) btn.classList.toggle("live", jobs.length > 0);
    let finished = false;
    ACTIVITY.lastActive.forEach((id) => { if (!ids.has(id)) finished = true; });
    ACTIVITY.lastActive = ids;
    if (finished) {
      window.dispatchEvent(new CustomEvent("nalarka:jobs-changed"));
      if (ACTIVITY.open) refreshJobList();
    }
  } catch (_) {}
}

document.addEventListener("DOMContentLoaded", () => {
  pollActiveJobs();
  ACTIVITY.timer = setInterval(pollActiveJobs, 3000);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && ACTIVITY.open) toggleActivity(false); });
});
