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
  configForm: document.getElementById("config-form"),
  progressDialog: document.getElementById("progress-dialog"),
  progressText: document.getElementById("progress-text"),
  progressBar: document.getElementById("progress-bar"),
  template: document.getElementById("entry-template"),
  gdClientId: document.getElementById("gd-client-id"),
  gdClientSecret: document.getElementById("gd-client-secret"),
  gdRefreshToken: document.getElementById("gd-refresh-token"),
  dbAccessToken: document.getElementById("db-access-token"),
};

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

  ui.progressText.textContent = "Copy started";
  ui.progressBar.style.width = "0%";
  ui.progressDialog.showModal();
  pollJob(result.job_id);
}

async function pollJob(jobId) {
  const timer = setInterval(async () => {
    try {
      const status = await api(`/api/copy/${encodeURIComponent(jobId)}`);
      ui.progressBar.style.width = `${status.percentage}%`;
      ui.progressText.textContent = `${status.status.toUpperCase()} - ${status.completed_items}/${status.total_items} ${
        status.current_item ? `| ${status.current_item}` : ""
      }`;

      if (status.status === "completed" || status.status === "failed") {
        clearInterval(timer);
        setTimeout(() => ui.progressDialog.close(), 500);
        if (status.status === "failed") {
          showError(status.error || "Copy failed");
        }
        state.source.selected.clear();
        await loadPane("source");
        await loadPane("destination");
      }
    } catch (err) {
      clearInterval(timer);
      ui.progressDialog.close();
      showError(err.message);
    }
  }, 500);
}

async function loadConfig() {
  const cfg = await api("/api/config");
  ui.gdClientId.value = cfg.google_drive_client_id || "";
  ui.gdClientSecret.value = cfg.google_drive_client_secret || "";
  ui.gdRefreshToken.value = cfg.google_drive_refresh_token || "";
  ui.dbAccessToken.value = cfg.dropbox_access_token || "";
}

async function saveConfig(event) {
  event.preventDefault();

  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      google_drive_client_id: ui.gdClientId.value,
      google_drive_client_secret: ui.gdClientSecret.value,
      google_drive_refresh_token: ui.gdRefreshToken.value,
      dropbox_access_token: ui.dbAccessToken.value,
    }),
  });

  ui.configDialog.close();
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
