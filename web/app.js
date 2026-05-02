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
  saveConfig: document.getElementById("save-config"),
  googleOauthStart: document.getElementById("google-oauth-start"),
  configForm: document.getElementById("config-form"),
  progressDialog: document.getElementById("progress-dialog"),
  progressState: document.getElementById("progress-state"),
  progressText: document.getElementById("progress-text"),
  progressBar: document.getElementById("progress-bar"),
  threadActivityWrap: document.getElementById("thread-activity-wrap"),
  threadActivityList: document.getElementById("thread-activity-list"),
  cancelCopy: document.getElementById("cancel-copy"),
  template: document.getElementById("entry-template"),
  gdServiceAccountJson: document.getElementById("gd-service-account-json"),
  gdClientId: document.getElementById("gd-client-id"),
  gdClientSecret: document.getElementById("gd-client-secret"),
  gdRefreshToken: document.getElementById("gd-refresh-token"),
  dbAccessToken: document.getElementById("db-access-token"),
  maxTransferThreads: document.getElementById("max-transfer-threads"),
};

let activeJobId = null;

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
  ui.progressDialog.showModal();
  pollJob(result.job_id);
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

async function pollJob(jobId) {
  const timer = setInterval(async () => {
    try {
      const status = await api(`/api/copy/${encodeURIComponent(jobId)}`);
      ui.progressBar.style.width = `${status.percentage}%`;
      renderThreadActivity(status);
      ui.progressText.textContent = `${status.status.toUpperCase()} - ${status.completed_items}/${status.total_items} ${
        status.current_item ? `| ${status.current_item}` : ""
      }`;

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

      if (status.status === "completed" || status.status === "failed") {
        activeJobId = null;
        ui.cancelCopy.disabled = true;
        clearInterval(timer);
        setTimeout(() => ui.progressDialog.close(), 500);
        if (status.status === "failed") {
          showError(status.error || "Copy failed");
        }
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
    dropbox_access_token: ui.dbAccessToken.value,
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
  ui.cancelCopy.addEventListener("click", () => cancelCopy().catch((err) => showError(err.message)));
  ui.googleOauthStart.addEventListener("click", () =>
    startGoogleOAuth().catch((err) => showError(err.message))
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
}

init().catch((err) => showError(err.message));
