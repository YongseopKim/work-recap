// api.js — fetch helper, job polling, utilities (no Alpine dependency)

/**
 * Generic API call helper.
 * @param {string} method - HTTP method
 * @param {string} path - path after /api (e.g. "/pipeline/run/2026-02-28")
 * @param {object|null} body - JSON body
 * @returns {Promise<Response>}
 */
export async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);

  const resp = await fetch(`/api${path}`, opts);
  if (!resp.ok && resp.status !== 404) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || err.detail || resp.statusText);
  }
  return resp;
}

/**
 * Poll a background job until terminal state.
 * @param {string} jobId
 * @param {function} onUpdate - called with job object on each poll
 */
export function pollJob(jobId, onUpdate) {
  const poll = async () => {
    try {
      const resp = await api("GET", `/pipeline/jobs/${jobId}`);
      const job = await resp.json();
      onUpdate(job);
      if (job.status === "completed" || job.status === "failed") return;
      setTimeout(poll, 1000);
    } catch {
      // On poll error, retry after delay
      setTimeout(poll, 2000);
    }
  };
  poll();
}

/**
 * Escape HTML special characters.
 * @param {string} str
 * @returns {string}
 */
export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/**
 * Copy text to clipboard. Returns true on success.
 * @param {string} text
 * @returns {Promise<boolean>}
 */
export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for non-secure contexts
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      return true;
    } catch {
      return false;
    } finally {
      document.body.removeChild(ta);
    }
  }
}

/** Status icons for job states. */
export const STATUS_ICONS = {
  accepted: "\u23f3",
  running: "\u23f3",
  completed: "\u2713",
  failed: "\u2717",
};

/**
 * Render job status as HTML string.
 * @param {object} job - { status, job_id, result, error }
 * @returns {string} HTML
 */
export function renderJobStatus(job) {
  const icon = STATUS_ICONS[job.status] || "";
  let html = `<span class="status-icon">${icon}</span>`;
  html += `<span class="status-${job.status}">`;

  if (job.status === "accepted" || job.status === "running") {
    html += `Running... <small>(job: ${job.job_id})</small>`;
  } else if (job.status === "completed") {
    html += "Completed";
    if (job.result) html += ` &mdash; ${escapeHtml(job.result)}`;
  } else if (job.status === "failed") {
    html += "Failed";
    if (job.error) html += ` &mdash; ${escapeHtml(job.error)}`;
  }

  html += "</span>";
  return html;
}

/**
 * Today's date as YYYY-MM-DD string.
 * @returns {string}
 */
export function todayStr() {
  return new Date().toISOString().slice(0, 10);
}
