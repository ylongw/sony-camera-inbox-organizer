const stateLabels = {
  starting: "启动中", idle: "空闲", scanning: "扫描中", processing: "转换中",
  paused: "已暂停", error: "错误", stopped: "已停止"
};

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return response.json();
}

async function refreshStatus() {
  const state = document.querySelector("#worker-state");
  if (!state) return;
  try {
    const data = await request("/api/status");
    state.textContent = stateLabels[data.state] || data.state;
    state.className = `state state-${data.state}`;
    document.querySelector("#automatic-state").textContent = data.automatic_enabled ? "开启" : "关闭";
    document.querySelector("#last-scan").textContent = data.last_scan_at || "尚未运行";
    const current = document.querySelector("#current-file");
    current.textContent = data.current_file || "无";
    current.title = data.current_file || "";
    const error = document.querySelector("#runtime-error");
    const message = data.last_error || data.path_errors.join("; ");
    error.textContent = message;
    error.classList.toggle("hidden", !message);
  } catch (error) {
    state.textContent = "连接失败";
    state.className = "state state-error";
  }
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value == null ? "" : String(value);
  return node.innerHTML;
}

async function refreshJobs() {
  const body = document.querySelector("#jobs-body");
  if (!body) return;
  const {jobs} = await request("/api/jobs");
  if (!jobs.length) {
    body.innerHTML = '<tr><td class="empty" colspan="6">暂无任务</td></tr>';
    return;
  }
  body.innerHTML = jobs.map((job) => {
    const elapsed = job.elapsed_seconds == null ? "-" : `${job.elapsed_seconds.toFixed(1)}s`;
    const retry = job.status === "failed"
      ? `<button class="icon-button retry-job" data-job-id="${job.id}" title="重试" aria-label="重试">↻</button>` : "";
    const detail = job.error || job.message;
    return `<tr>
      <td><span class="badge badge-${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
      <td class="file-cell" title="${escapeHtml(job.source_path)}">${escapeHtml(job.source_path)}</td>
      <td>${job.marks}</td><td>${elapsed}</td><td>${escapeHtml(job.finished_at || job.started_at || "-")}</td><td>${retry}</td>
    </tr>${detail ? `<tr class="detail-row"><td></td><td colspan="5">${escapeHtml(detail)}</td></tr>` : ""}`;
  }).join("");
}

document.addEventListener("click", async (event) => {
  const scan = event.target.closest("#scan-now");
  if (scan) {
    scan.disabled = true;
    try { await request("/api/scan", {method: "POST"}); await refreshStatus(); }
    finally { window.setTimeout(() => { scan.disabled = false; }, 1000); }
  }
  const refresh = event.target.closest("#refresh-jobs");
  if (refresh) await refreshJobs();
  const retry = event.target.closest(".retry-job");
  if (retry) {
    retry.disabled = true;
    await request(`/api/jobs/${retry.dataset.jobId}/retry`, {method: "POST"});
    await refreshStatus();
  }
});

if (document.querySelector("#worker-state")) {
  refreshStatus();
  window.setInterval(refreshStatus, 2000);
  window.setInterval(refreshJobs, 10000);
}
