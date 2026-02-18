// work-recap frontend

// ── API Helper ──

async function api(method, path, body = null) {
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

async function pollJob(jobId, onUpdate) {
  const poll = async () => {
    const resp = await api("GET", `/pipeline/jobs/${jobId}`);
    const job = await resp.json();
    onUpdate(job);
    if (job.status === "completed" || job.status === "failed") return;
    setTimeout(poll, 1000);
  };
  poll();
}

// ── Status Rendering ──

const STATUS_ICONS = {
  accepted: "\u23f3",  // hourglass
  running: "\u23f3",
  completed: "\u2713", // checkmark
  failed: "\u2717",    // cross
};

function renderJobStatus(job) {
  const icon = STATUS_ICONS[job.status] || "";
  let html = `<span class="status-icon">${icon}</span>`;
  html += `<span class="status-${job.status}">`;

  if (job.status === "accepted" || job.status === "running") {
    html += `Running... <small>(job: ${job.job_id})</small>`;
  } else if (job.status === "completed") {
    html += `Completed`;
    if (job.result) html += ` &mdash; ${escapeHtml(job.result)}`;
  } else if (job.status === "failed") {
    html += `Failed`;
    if (job.error) html += ` &mdash; ${escapeHtml(job.error)}`;
  }

  html += "</span>";
  return html;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Tab Switching ──

function switchTab(tabName) {
  document.querySelectorAll(".tab-link").forEach((link) => {
    link.classList.toggle("active", link.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-content").forEach((section) => {
    section.classList.toggle("active", section.id === `tab-${tabName}`);
  });
}

// ── Pipeline Tab ──

async function runPipeline() {
  const dateInput = document.getElementById("pipeline-date");
  const date = dateInput.value;
  if (!date) { alert("Please select a date."); return; }

  const btn = document.getElementById("btn-run");
  const statusEl = document.getElementById("pipeline-status");
  const contentEl = document.getElementById("pipeline-status-content");

  btn.setAttribute("aria-busy", "true");
  statusEl.classList.remove("hidden");
  contentEl.innerHTML = renderJobStatus({ status: "accepted", job_id: "..." });

  try {
    const resp = await api("POST", `/pipeline/run/${date}`);
    const { job_id } = await resp.json();

    pollJob(job_id, (job) => {
      contentEl.innerHTML = renderJobStatus(job);
      if (job.status === "completed") {
        contentEl.innerHTML += `<br><a class="view-summary-link" onclick="viewDailySummary('${date}')">View Summary</a>`;
      }
      if (job.status === "completed" || job.status === "failed") {
        btn.removeAttribute("aria-busy");
      }
    });
  } catch (e) {
    contentEl.innerHTML = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
    btn.removeAttribute("aria-busy");
  }
}

async function runRange() {
  const since = document.getElementById("pipeline-since").value;
  const until = document.getElementById("pipeline-until").value;
  if (!since || !until) { alert("Please select both dates."); return; }

  const btn = document.getElementById("btn-run-range");
  const statusEl = document.getElementById("pipeline-status");
  const contentEl = document.getElementById("pipeline-status-content");

  btn.setAttribute("aria-busy", "true");
  statusEl.classList.remove("hidden");
  contentEl.innerHTML = renderJobStatus({ status: "accepted", job_id: "..." });

  try {
    const resp = await api("POST", "/pipeline/run/range", { since, until });
    const { job_id } = await resp.json();

    pollJob(job_id, (job) => {
      contentEl.innerHTML = renderJobStatus(job);
      if (job.status === "completed" || job.status === "failed") {
        btn.removeAttribute("aria-busy");
      }
    });
  } catch (e) {
    contentEl.innerHTML = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
    btn.removeAttribute("aria-busy");
  }
}

function viewDailySummary(date) {
  document.getElementById("summary-type").value = "daily";
  updateSummaryInputs();
  const dateInput = document.querySelector("#summary-inputs input[type='date']");
  if (dateInput) dateInput.value = date;
  switchTab("summaries");
  viewSummary();
}

// ── Summaries Tab ──

function updateSummaryInputs() {
  const type = document.getElementById("summary-type").value;
  const container = document.getElementById("summary-inputs");

  const templates = {
    daily: `<label>Date<input type="date" id="summary-date"></label>`,
    weekly: `<label>Year<input type="number" id="summary-year" min="2020" max="2099"></label>
             <label>Week<input type="number" id="summary-week" min="1" max="53"></label>`,
    monthly: `<label>Year<input type="number" id="summary-year" min="2020" max="2099"></label>
              <label>Month<input type="number" id="summary-month" min="1" max="12"></label>`,
    yearly: `<label>Year<input type="number" id="summary-year" min="2020" max="2099"></label>`,
  };

  container.innerHTML = templates[type] || "";
}

function getSummaryPath() {
  const type = document.getElementById("summary-type").value;

  if (type === "daily") {
    const date = document.getElementById("summary-date")?.value;
    return date ? `/summary/daily/${date}` : null;
  }
  if (type === "weekly") {
    const year = document.getElementById("summary-year")?.value;
    const week = document.getElementById("summary-week")?.value;
    return year && week ? `/summary/weekly/${year}/${week}` : null;
  }
  if (type === "monthly") {
    const year = document.getElementById("summary-year")?.value;
    const month = document.getElementById("summary-month")?.value;
    return year && month ? `/summary/monthly/${year}/${month}` : null;
  }
  if (type === "yearly") {
    const year = document.getElementById("summary-year")?.value;
    return year ? `/summary/yearly/${year}` : null;
  }
  return null;
}

async function viewSummary() {
  const path = getSummaryPath();
  if (!path) { alert("Please fill in all fields."); return; }

  const viewer = document.getElementById("summary-viewer");
  const content = document.getElementById("summary-content");
  const errorEl = document.getElementById("summary-error");

  viewer.classList.add("hidden");
  errorEl.classList.add("hidden");

  try {
    const resp = await api("GET", path);
    if (resp.status === 404) {
      errorEl.textContent = "Summary not found. Run the pipeline first.";
      errorEl.classList.remove("hidden");
      return;
    }
    const data = await resp.json();
    content.innerHTML = marked.parse(data.content);
    viewer.classList.remove("hidden");
  } catch (e) {
    errorEl.textContent = e.message;
    errorEl.classList.remove("hidden");
  }
}

// ── Ask Tab ──

async function askQuestion() {
  const question = document.getElementById("ask-question").value.trim();
  if (!question) { alert("Please enter a question."); return; }

  const months = parseInt(document.getElementById("ask-months").value, 10) || 3;
  const btn = document.getElementById("btn-ask");
  const statusEl = document.getElementById("ask-status");
  const statusContent = document.getElementById("ask-status-content");
  const resultEl = document.getElementById("ask-result");
  const resultContent = document.getElementById("ask-result-content");

  btn.setAttribute("aria-busy", "true");
  statusEl.classList.remove("hidden");
  resultEl.classList.add("hidden");
  statusContent.innerHTML = renderJobStatus({ status: "accepted", job_id: "..." });

  try {
    const resp = await api("POST", "/query", { question, months });
    const { job_id } = await resp.json();

    pollJob(job_id, (job) => {
      statusContent.innerHTML = renderJobStatus(job);

      if (job.status === "completed" && job.result) {
        resultContent.innerHTML = marked.parse(job.result);
        resultEl.classList.remove("hidden");
        statusEl.classList.add("hidden");
      }
      if (job.status === "completed" || job.status === "failed") {
        btn.removeAttribute("aria-busy");
      }
    });
  } catch (e) {
    statusContent.innerHTML = `<span class="status-failed">${escapeHtml(e.message)}</span>`;
    btn.removeAttribute("aria-busy");
  }
}

// ── Init ──

function init() {
  // Set default date to today
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("pipeline-date").value = today;
  document.getElementById("pipeline-until").value = today;

  // Tab switching
  document.querySelectorAll(".tab-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      switchTab(link.dataset.tab);
    });
  });

  // Pipeline
  document.getElementById("btn-run").addEventListener("click", runPipeline);
  document.getElementById("btn-run-range").addEventListener("click", runRange);

  // Summaries
  document.getElementById("summary-type").addEventListener("change", updateSummaryInputs);
  document.getElementById("btn-view-summary").addEventListener("click", viewSummary);
  updateSummaryInputs();

  // Ask
  document.getElementById("btn-ask").addEventListener("click", askQuestion);
  document.getElementById("ask-question").addEventListener("keydown", (e) => {
    if (e.key === "Enter") askQuestion();
  });
}

document.addEventListener("DOMContentLoaded", init);
