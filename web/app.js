const PROVIDERS = [
  { value: "local", label: "Local Machine" },
  { value: "gdrive", label: "Google Drive" },
  { value: "dropbox", label: "Dropbox" },
];

const state = {
  source: {
    provider: "local",
    path: null,
    entries: [],
    selected: new Set(),
    stack: [],
  },
  destination: {
    provider: "local",
    path: null,
    entries: [],
    stack: [],
  },
};

const ui = {
  sourceProvider: document.getElementById("source-provider"),
  destinationProvider: document.getElementById("destination-provider"),
  sourceList: document.getElementById("source-list"),
  destinationList: document.getElementById("destination-list"),
  sourcePath: document.getElementById("source-path"),
  destinationPath: document.getElementById("destination-path"),
  sourceUp: document.getElementById("source-up"),
  destinationUp: document.getElementById("destination-up"),
  sourceRefresh: document.getElementById("source-refresh"),
  destinationRefresh: document.getElementById("destination-refresh"),
  sourceMkdir: document.getElementById("source-mkdir"),
  destinationMkdir: document.getElementById("destination-mkdir"),
  copyBtn: document.getElementById("copy-btn"),
  configDialog: document.getElementById("config-dialog"),
  openConfig: document.getElementById("open-config"),
  resumeBanner: document.getElementById("resume-banner"),
  resumeJobsList: document.getElementById("resume-jobs-list"),
  resumeRefresh: document.getElementById("resume-refresh"),
  resumeOpenManifests: document.getElementById("resume-open-manifests"),
  saveConfig: document.getElementById("save-config"),
  googleOauthStart: document.getElementById("google-oauth-start"),
  dropboxOauthStart: document.getElementById("dropbox-oauth-start"),
  configForm: document.getElementById("config-form"),
  progressDialog: document.getElementById("progress-dialog"),
  progressState: document.getElementById("progress-state"),
  progressText: document.getElementById("progress-text"),
  progressBar: document.getElementById("progress-bar"),
  threadActivityWrap: document.getElementById("thread-activity-wrap"),
  threadActivityList: document.getElementById("thread-activity-list"),
  openManifests: document.getElementById("open-manifests"),
  cancelCopy: document.getElementById("cancel-copy"),
  resumeCopy: document.getElementById("resume-copy"),
  template: document.getElementById("entry-template"),
  gdServiceAccountJson: document.getElementById("gd-service-account-json"),
  gdClientId: document.getElementById("gd-client-id"),
  gdClientSecret: document.getElementById("gd-client-secret"),
  gdRefreshToken: document.getElementById("gd-refresh-token"),
  dbAppCredentialsJson: document.getElementById("db-app-credentials-json"),
  dbAppKey: document.getElementById("db-app-key"),
  dbAppSecret: document.getElementById("db-app-secret"),
  dbRefreshToken: document.getElementById("db-refresh-token"),
  dbAccessToken: document.getElementById("db-access-token"),
  maxTransferThreads: document.getElementById("max-transfer-threads"),
};

let activeJobId = null;
let resumableJobId = null;

function setProgressState(kind, text) {
  ui.progressState.className = `progress-state ${kind}`;
  ui.progressState.textContent = text;
}

function renderThreadActivity(status) {
  const workers = Number(status.worker_count || 1);
  const activity = Array.isArray(status.thread_activity) ? status.thread_activity : [];

  if (workers <= 1) {
    ui.threadActivityWrap.hidden = true;
    ui.threadActivityList.innerHTML = "";
    return;
  }

  ui.threadActivityWrap.hidden = false;
  ui.threadActivityList.innerHTML = "";

  if (activity.length === 0) {
    const li = document.createElement("li");
    li.textContent = "Waiting for worker activity...";
    ui.threadActivityList.appendChild(li);
    return;
  }

  for (const row of activity) {
    const li = document.createElement("li");
    const thread = row.thread || "worker";
    const item = row.item || "idle";
    li.textContent = `${thread}: ${item}`;
    ui.threadActivityList.appendChild(li);
  }
}

function showError(message) {
  window.alert(message);
}

function prettyPath(path, provider) {
  if (!path) {
    return provider === "local" ? "Home" : "Root";
  }
  return path;
}

function fileSize(value) {
  if (value === undefined || value === null) {
    return "";
  }
  const units = ["B", "KB", "MB", "GB"];
  let n = value;
  let idx = 0;
  while (n >= 1024 && idx < units.length - 1) {
    n /= 1024;
    idx += 1;
  }
  return `${n.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // Keep fallback detail.
    }
    throw new Error(detail);
  }
  return response.json();
}

function populateProviders(selectEl) {
  selectEl.innerHTML = "";
  for (const p of PROVIDERS) {
    const opt = document.createElement("option");
    opt.value = p.value;
    opt.textContent = p.label;
    selectEl.appendChild(opt);
  }
}

async function loadPane(kind) {
  const pane = state[kind];
  const result = await api(
    `/api/list?provider=${encodeURIComponent(pane.provider)}&parent_id=${encodeURIComponent(
      pane.path || ""
    )}`
  );
  pane.entries = result.entries;
  renderPane(kind);
}

function goInto(kind, entry) {
  const pane = state[kind];
  pane.stack.push(pane.path);
  pane.path = entry.id;
  if (kind === "source") {
    pane.selected.clear();
  }
  loadPane(kind).catch((err) => showError(err.message));
}

function goUp(kind) {
  const pane = state[kind];
  if (pane.stack.length === 0) {
    pane.path = null;
  } else {
    pane.path = pane.stack.pop() || null;
  }
  if (kind === "source") {
    pane.selected.clear();
  }
  loadPane(kind).catch((err) => showError(err.message));
}

function resetPane(kind, provider) {
  const pane = state[kind];
  pane.provider = provider;
  pane.path = null;
  pane.entries = [];
  pane.stack = [];
  if (kind === "source") {
    pane.selected.clear();
  }
  loadPane(kind).catch((err) => showError(err.message));
}

function renderPane(kind) {
  const pane = state[kind];
  const listEl = kind === "source" ? ui.sourceList : ui.destinationList;
  const pathEl = kind === "source" ? ui.sourcePath : ui.destinationPath;
  pathEl.textContent = prettyPath(pane.path, pane.provider);

  listEl.innerHTML = "";

  for (const entry of pane.entries) {
    const node = ui.template.content.firstElementChild.cloneNode(true);
    const checkbox = node.querySelector(".entry-checkbox");
    const openBtn = node.querySelector(".entry-open");
    const meta = node.querySelector(".entry-meta");

    openBtn.textContent = entry.is_folder ? `📁 ${entry.name}` : entry.name;
    openBtn.classList.toggle("folder", entry.is_folder);
    meta.textContent = entry.is_folder ? "folder" : fileSize(entry.size);

    if (kind !== "source") {
      checkbox.disabled = true;
      checkbox.style.visibility = "hidden";
    } else {
      checkbox.checked = pane.selected.has(entry.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          pane.selected.add(entry.id);
        } else {
          pane.selected.delete(entry.id);
        }
      });
    }

    openBtn.addEventListener("click", () => {
      if (entry.is_folder) {
        goInto(kind, entry);
      }
    });

    listEl.appendChild(node);
  }
}

async function createFolder(kind) {
  const pane = state[kind];
  const name = window.prompt("Folder name");
  if (!name) {
    return;
  }

  await api("/api/folder", {
    method: "POST",
    body: JSON.stringify({
      provider: pane.provider,
      parent_id: pane.path,
      name,
    }),
  });

  await loadPane(kind);
}

async function startCopy() {
  const source = state.source;
  if (source.selected.size === 0) {
    showError("Select at least one source item");
    return;
  }

  const selectedEntries = source.entries.filter((e) => source.selected.has(e.id));
  const payload = {
    source_provider: source.provider,
    destination_provider: state.destination.provider,
    source_parent_id: source.path,
    destination_parent_id: state.destination.path,
    selections: selectedEntries.map((entry) => ({
      id: entry.id,
      name: entry.name,
      is_folder: entry.is_folder,
    })),
  };

  const result = await api("/api/copy/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  activeJobId = result.job_id;
  setProgressState("running", "Running");
  ui.progressText.textContent = "Copy started";
  ui.progressBar.style.width = "0%";
  ui.threadActivityWrap.hidden = true;
  ui.threadActivityList.innerHTML = "";
  ui.cancelCopy.disabled = false;
  ui.cancelCopy.textContent = "Cancel";
  ui.resumeCopy.hidden = true;
  resumableJobId = null;
  ui.progressDialog.showModal();
  pollJob(result.job_id);
}

function renderResumableJobsBanner(jobs) {
  if (!Array.isArray(jobs) || jobs.length === 0) {
    ui.resumeBanner.hidden = true;
    ui.resumeJobsList.innerHTML = "";
    return;
  }

  ui.resumeBanner.hidden = false;
  ui.resumeJobsList.innerHTML = "";

  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = "resume-job-row";

    const text = document.createElement("div");
    const title = document.createElement("div");
    title.textContent = `Job ${job.job_id}`;
    const meta = document.createElement("div");
    meta.className = "resume-job-meta";
    meta.textContent = `status=${job.status} pending=${job.pending_files} verify=${job.verify_status} src=${job.source_provider} dst=${job.destination_provider}`;
    text.appendChild(title);
    text.appendChild(meta);

    const button = document.createElement("button");
    button.className = "btn btn-small btn-accent";
    button.type = "button";
    button.textContent = "Resume";
    button.addEventListener("click", () => {
      resumeCopy(job.job_id).catch((err) => showError(err.message));
    });

    row.appendChild(text);
    row.appendChild(button);
    ui.resumeJobsList.appendChild(row);
  }
}

async function checkResumableJobsOnStartup() {
  const result = await api("/api/copy/resumable");
  const jobs = Array.isArray(result.jobs) ? result.jobs : [];
  renderResumableJobsBanner(jobs);
}

async function resumeCopy(jobId = null) {
  const targetJobId = jobId || resumableJobId;
  if (!targetJobId) {
    return;
  }

  const result = await api("/api/copy/resume", {
    method: "POST",
    body: JSON.stringify({ job_id: targetJobId }),
  });

  activeJobId = result.job_id;
  resumableJobId = null;
  ui.resumeCopy.hidden = true;
  ui.cancelCopy.disabled = false;
  ui.cancelCopy.textContent = "Cancel";
  setProgressState("running", "Running");
  ui.progressText.textContent = "Resumed copy started";
  ui.progressDialog.showModal();
  pollJob(result.job_id);
  await checkResumableJobsOnStartup();
}

async function cancelCopy() {
  if (!activeJobId) {
    return;
  }
  ui.cancelCopy.disabled = true;
  ui.cancelCopy.textContent = "Cancelling...";
  setProgressState("cancel-requested", "Cancel Requested");
  await api(`/api/copy/${encodeURIComponent(activeJobId)}/cancel`, {
    method: "POST",
  });
}

async function openManifestFolder() {
  await api("/api/jobs/open-manifests", { method: "POST" });
}

async function pollJob(jobId) {
  const timer = setInterval(async () => {
    try {
      const status = await api(`/api/copy/${encodeURIComponent(jobId)}`);
      ui.progressBar.style.width = `${status.percentage}%`;
      renderThreadActivity(status);
      if (status.status === "verifying") {
        ui.progressText.textContent = `VERIFYING - ${status.verify_completed}/${status.verify_total}`;
      } else {
        ui.progressText.textContent = `${status.status.toUpperCase()} - ${status.completed_items}/${status.total_items} ${
          status.current_item ? `| ${status.current_item}` : ""
        }`;
      }

      if (status.status === "cancelled") {
        setProgressState("cancelled", "Cancelled");
      } else if (status.status === "completed") {
        setProgressState("completed", "Completed");
      } else if (status.status === "failed") {
        setProgressState("failed", "Failed");
      } else if (status.cancel_requested) {
        setProgressState("cancel-requested", "Cancel Requested");
      } else {
        setProgressState("running", "Running");
      }

      if (status.status === "verifying") {
        ui.cancelCopy.disabled = false;
        ui.cancelCopy.textContent = "Cancel Verify";
      } else if (status.status === "running") {
        ui.cancelCopy.textContent = "Cancel";
      }

      if (status.status === "failed" && status.resumable_job_id) {
        resumableJobId = status.resumable_job_id;
        ui.resumeCopy.hidden = false;
      }

      if (status.status === "completed" || status.status === "failed") {
        activeJobId = null;
        ui.cancelCopy.disabled = status.status !== "failed";
        clearInterval(timer);
        if (status.status === "failed") {
          showError(status.error || "Copy failed");
          return;
        }
        setTimeout(() => ui.progressDialog.close(), 500);
        state.source.selected.clear();
        await loadPane("source");
        await loadPane("destination");
      } else if (status.status === "cancelled") {
        activeJobId = null;
        ui.cancelCopy.disabled = true;
        clearInterval(timer);
        setTimeout(() => ui.progressDialog.close(), 500);
        state.source.selected.clear();
        await loadPane("source");
        await loadPane("destination");
      }
    } catch (err) {
      activeJobId = null;
      ui.cancelCopy.disabled = true;
      clearInterval(timer);
      ui.progressDialog.close();
      showError(err.message);
    }
  }, 500);
}

async function loadConfig() {
  const cfg = await api("/api/config");
  ui.gdServiceAccountJson.value = cfg.google_drive_service_account_json || "";
  ui.gdClientId.value = cfg.google_drive_client_id || "";
  ui.gdClientSecret.value = cfg.google_drive_client_secret || "";
  ui.gdRefreshToken.value = cfg.google_drive_refresh_token || "";
  ui.dbAppCredentialsJson.value = cfg.dropbox_app_credentials_json || "";
  ui.dbAppKey.value = cfg.dropbox_app_key || "";
  ui.dbAppSecret.value = cfg.dropbox_app_secret || "";
  ui.dbRefreshToken.value = cfg.dropbox_refresh_token || "";
  ui.dbAccessToken.value = cfg.dropbox_access_token || "";
  ui.maxTransferThreads.value = cfg.max_transfer_threads || 5;
}

async function saveConfig(event) {
  event.preventDefault();

  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      google_drive_service_account_json: ui.gdServiceAccountJson.value,
      google_drive_client_id: ui.gdClientId.value,
      google_drive_client_secret: ui.gdClientSecret.value,
      google_drive_refresh_token: ui.gdRefreshToken.value,
      dropbox_app_credentials_json: ui.dbAppCredentialsJson.value,
      dropbox_app_key: ui.dbAppKey.value,
      dropbox_app_secret: ui.dbAppSecret.value,
      dropbox_refresh_token: ui.dbRefreshToken.value,
      dropbox_access_token: ui.dbAccessToken.value,
      max_transfer_threads: Number(ui.maxTransferThreads.value || 5),
    }),
  });

  ui.configDialog.close();
}

async function startGoogleOAuth() {
  const payload = {
    google_drive_service_account_json: ui.gdServiceAccountJson.value,
    google_drive_client_id: ui.gdClientId.value,
    google_drive_client_secret: ui.gdClientSecret.value,
    google_drive_refresh_token: ui.gdRefreshToken.value,
    dropbox_app_credentials_json: ui.dbAppCredentialsJson.value,
    dropbox_app_key: ui.dbAppKey.value,
    dropbox_app_secret: ui.dbAppSecret.value,
    dropbox_refresh_token: ui.dbRefreshToken.value,
    dropbox_access_token: ui.dbAccessToken.value,
    max_transfer_threads: Number(ui.maxTransferThreads.value || 5),
  };

  const result = await api("/api/google/oauth/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  const popup = window.open(result.authorization_url, "google-oauth", "width=720,height=780");
  if (!popup) {
    throw new Error("Popup was blocked. Allow popups for this site and try again.");
  }

  const timer = setInterval(async () => {
    if (!popup.closed) {
      return;
    }

    clearInterval(timer);
    await loadConfig();
    window.alert("Google OAuth finished. The refresh token has been loaded into the form.");
  }, 800);
}

async function startDropboxOAuth() {
  const payload = {
    google_drive_service_account_json: ui.gdServiceAccountJson.value,
    google_drive_client_id: ui.gdClientId.value,
    google_drive_client_secret: ui.gdClientSecret.value,
    google_drive_refresh_token: ui.gdRefreshToken.value,
    dropbox_app_credentials_json: ui.dbAppCredentialsJson.value,
    dropbox_app_key: ui.dbAppKey.value,
    dropbox_app_secret: ui.dbAppSecret.value,
    dropbox_refresh_token: ui.dbRefreshToken.value,
    dropbox_access_token: ui.dbAccessToken.value,
    max_transfer_threads: Number(ui.maxTransferThreads.value || 5),
  };

  const result = await api("/api/dropbox/oauth/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  const popup = window.open(result.authorization_url, "dropbox-oauth", "width=720,height=780");
  if (!popup) {
    throw new Error("Popup was blocked. Allow popups for this site and try again.");
  }

  const timer = setInterval(async () => {
    if (!popup.closed) {
      return;
    }

    clearInterval(timer);
    await loadConfig();
    window.alert("Dropbox OAuth finished. Tokens have been loaded into the form.");
  }, 800);
}

function wireEvents() {
  ui.sourceProvider.addEventListener("change", () => resetPane("source", ui.sourceProvider.value));
  ui.destinationProvider.addEventListener("change", () =>
    resetPane("destination", ui.destinationProvider.value)
  );

  ui.sourceUp.addEventListener("click", () => goUp("source"));
  ui.destinationUp.addEventListener("click", () => goUp("destination"));

  ui.sourceRefresh.addEventListener("click", () => loadPane("source").catch((err) => showError(err.message)));
  ui.destinationRefresh.addEventListener("click", () =>
    loadPane("destination").catch((err) => showError(err.message))
  );

  ui.sourceMkdir.addEventListener("click", () => createFolder("source").catch((err) => showError(err.message)));
  ui.destinationMkdir.addEventListener("click", () =>
    createFolder("destination").catch((err) => showError(err.message))
  );

  ui.copyBtn.addEventListener("click", () => startCopy().catch((err) => showError(err.message)));

  ui.openConfig.addEventListener("click", async () => {
    await loadConfig();
    ui.configDialog.showModal();
  });

  ui.saveConfig.addEventListener("click", saveConfig);
  ui.resumeRefresh.addEventListener("click", () =>
    checkResumableJobsOnStartup().catch((err) => showError(err.message))
  );
  ui.resumeOpenManifests.addEventListener("click", () =>
    openManifestFolder().catch((err) => showError(err.message))
  );
  ui.openManifests.addEventListener("click", () =>
    openManifestFolder().catch((err) => showError(err.message))
  );
  ui.cancelCopy.addEventListener("click", () => cancelCopy().catch((err) => showError(err.message)));
  ui.resumeCopy.addEventListener("click", () => resumeCopy().catch((err) => showError(err.message)));
  ui.googleOauthStart.addEventListener("click", () =>
    startGoogleOAuth().catch((err) => showError(err.message))
  );
  ui.dropboxOauthStart.addEventListener("click", () =>
    startDropboxOAuth().catch((err) => showError(err.message))
  );
}

async function init() {
  populateProviders(ui.sourceProvider);
  populateProviders(ui.destinationProvider);

  ui.sourceProvider.value = state.source.provider;
  ui.destinationProvider.value = state.destination.provider;

  wireEvents();
  await loadPane("source");
  await loadPane("destination");
  await checkResumableJobsOnStartup();
}

init().catch((err) => showError(err.message));
