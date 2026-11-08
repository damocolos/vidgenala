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

// SSE Job Connection
function connectJobEvents(jobId, options = {}) {
  const {
    consoleId = "live-console",
    progressId = "job-progress",
    statusId = "job-status",
    onDone = null,
    onError = null,
  } = options;

  const consoleEl = document.getElementById(consoleId);
  const progressEl = document.getElementById(progressId);
  const statusEl = document.getElementById(statusId);

  if (consoleEl) {
    consoleEl.textContent = `[System] Connected to job: ${jobId}\n`;
  }
  if (progressEl) {
    progressEl.value = 0;
  }
  if (statusEl) {
    statusEl.textContent = "Running...";
    statusEl.className = "badge badge-orange";
  }

  const evtSource = new EventSource(`/api/jobs/${jobId}/events`);

  evtSource.addEventListener("message", (e) => {
    try {
      const data = JSON.parse(e.data);
      if (consoleEl && data.message) {
        consoleEl.textContent += data.message + "\n";
        consoleEl.scrollTop = consoleEl.scrollHeight;
      }
      if (progressEl && typeof data.pct === "number") {
        progressEl.value = Math.max(0, Math.min(100, data.pct));
      }
      if (statusEl && data.step) {
        statusEl.textContent = data.step;
      }
    } catch (err) {
      if (consoleEl) {
        consoleEl.textContent += e.data + "\n";
        consoleEl.scrollTop = consoleEl.scrollHeight;
      }
    }
  });

  evtSource.addEventListener("done", (e) => {
    evtSource.close();
    let data = null;
    try {
      data = e.data ? JSON.parse(e.data) : null;
    } catch (_) {}

    if (data && (data.status === "failed" || data.status === "canceled" || data.error)) {
      if (statusEl) {
        statusEl.textContent = data.status === "canceled" ? "Canceled" : "Failed";
        statusEl.className = "badge badge-red";
      }
      showToast(`Job ${data.status || 'failed'}: ${data.error || 'Unknown error'}`, "error");
      if (onError) onError(data);
      return;
    }

    if (progressEl) progressEl.value = 100;
    if (statusEl) {
      statusEl.textContent = "Completed";
      statusEl.className = "badge badge-green";
    }
    showToast("Job completed successfully!", "success");
    if (onDone) onDone(data);
  });

  evtSource.addEventListener("error", (e) => {
    // If the server closed stream or job errored
    try {
      const data = e.data ? JSON.parse(e.data) : null;
      if (data && data.error) {
        evtSource.close();
        if (statusEl) {
          statusEl.textContent = "Failed";
          statusEl.className = "badge badge-red";
        }
        showToast(`Job failed: ${data.error}`, "error");
        if (onError) onError(data);
        return;
      }
    } catch (_) {}
  });

  return evtSource;
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
    let checksHtml = "";
    for (let i = 1; i <= 7; i++) {
      const key = `check_${i}`;
      const passed = data[key];
      const badge = passed ? '<span class="badge badge-green">PASS</span>' : '<span class="badge badge-red">FAIL</span>';
      checksHtml += `<tr><td>Check ${i}</td><td>${badge}</td><td>${data.details_json?.[key] || "-"}</td></tr>`;
    }

    content.innerHTML = `
      <div style="margin-bottom: 1rem;">
        <strong>Overall:</strong> ${data.passed ? '<span class="badge badge-green">7/7 PASSED</span>' : '<span class="badge badge-red">FAILED</span>'}
      </div>
      <table class="table">
        <thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>
        <tbody>${checksHtml}</tbody>
      </table>
    `;
  } catch (e) {
    content.innerHTML = `<p class="text-danger">Error: ${e.message}</p>`;
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
