from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.models import CopySelection
from app.providers import ProviderError, StorageProvider


class CopyCancelled(RuntimeError):
    pass


@dataclass
class FileCopyTask:
    entry_id: str
    destination_parent: str | None
    name: str
    size: int | None = None

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "destination_parent": self.destination_parent,
            "name": self.name,
            "size": self.size,
        }

    @staticmethod
    def from_dict(payload: dict) -> "FileCopyTask":
        return FileCopyTask(
            entry_id=payload.get("entry_id", ""),
            destination_parent=payload.get("destination_parent"),
            name=payload.get("name", ""),
            size=payload.get("size"),
        )


@dataclass
class JobState:
    id: str
    status: str = "queued"
    message: str = ""
    total_items: int = 0
    completed_items: int = 0
    current_item: str = ""
    error: str = ""
    cancel_requested: bool = False
    worker_count: int = 1
    thread_activity: dict[str, str] = field(default_factory=dict)
    thread_slots: dict[str, int] = field(default_factory=dict)
    verify_status: str = "pending"
    verify_total: int = 0
    verify_completed: int = 0
    verify_missing: int = 0
    verify_cancel_requested: bool = False
    resumable_job_id: str | None = None
    manifest_path: str = ""
    pending_files: int = 0
    copied_files: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    verify_cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    large_file_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


LARGE_FILE_THRESHOLD_BYTES = 64 * 1024 * 1024
MANIFESTS_DIR = Path(__file__).resolve().parent.parent / ".storageman_jobs"


def _ensure_manifests_dir() -> Path:
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    return MANIFESTS_DIR


def get_manifests_dir() -> Path:
    return _ensure_manifests_dir()


def _manifest_path(job_id: str) -> Path:
    return _ensure_manifests_dir() / f"{job_id}.json"


def _read_manifest(job_id: str) -> dict:
    path = _manifest_path(job_id)
    if not path.exists():
        raise ProviderError("No resumable job state found")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(job_id: str, payload: dict) -> None:
    path = _manifest_path(job_id)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_resumable_manifests() -> list[dict]:
    folder = _ensure_manifests_dir()
    summaries: list[dict] = []
    for file_path in folder.glob("*.json"):
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        status = payload.get("status", "unknown")
        verify_status = payload.get("verify_status", "pending")
        pending_files = len(payload.get("pending_tasks", []))
        copied_files = len([x for x in payload.get("copied_items", []) if not x.get("is_folder")])
        missing_verify_items = len(payload.get("verify_missing", []))
        is_resumable = status in {"failed", "cancelled", "running", "verifying"}
        has_resumeable_work = pending_files > 0
        has_resumeable_work = has_resumeable_work or verify_status in {"pending", "running", "cancelled"}
        has_resumeable_work = has_resumeable_work or (status in {"failed", "cancelled"} and copied_files > 0)
        has_resumeable_work = has_resumeable_work or missing_verify_items > 0
        is_resumable = is_resumable and has_resumeable_work
        if not is_resumable:
            continue

        summaries.append(
            {
                "job_id": payload.get("job_id", file_path.stem),
                "status": status,
                "pending_files": pending_files,
                "copied_files": copied_files,
                "verify_status": verify_status,
                "verify_missing": missing_verify_items,
                "source_provider": payload.get("source_provider", "local"),
                "destination_provider": payload.get("destination_provider", "local"),
                "updated_at": file_path.stat().st_mtime,
            }
        )

    summaries.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return summaries


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create(self) -> JobState:
        state = JobState(id=str(uuid.uuid4()))
        with self._lock:
            self._jobs[state.id] = state
        return state

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)

        if not job:
            return False

        with job.lock:
            if job.status in {"completed", "failed", "cancelled"}:
                return True
            job.cancel_requested = True
            job.message = "Cancellation requested"
        job.cancel_event.set()
        return True


registry = JobRegistry()


def start_copy_job(
    source: StorageProvider,
    destination: StorageProvider,
    source_provider: str,
    destination_provider: str,
    source_parent_id: str | None,
    destination_parent_id: str | None,
    selections: list[CopySelection],
    max_threads: int,
) -> JobState:
    state = registry.create()
    state.worker_count = max(1, max_threads)
    state.thread_activity = {
        f"Worker {i}": "idle (no files copied yet)"
        for i in range(1, state.worker_count + 1)
    }
    state.resumable_job_id = state.id
    state.manifest_path = str(_manifest_path(state.id))

    def run() -> None:
        manifest = {
            "job_id": state.id,
            "source_provider": source_provider,
            "destination_provider": destination_provider,
            "source_parent_id": source_parent_id,
            "destination_parent_id": destination_parent_id,
            "max_threads": state.worker_count,
            "status": "running",
            "pending_tasks": [],
            "copied_items": [],
            "verify_ids": [],
            "verify_status": "pending",
            "verify_missing": [],
        }
        with state.lock:
            state.status = "running"
        try:
            state.total_items = _count_work_items(source, selections)

            file_tasks: list[FileCopyTask] = []
            for selected in selections:
                _ensure_not_cancelled(state)
                _prepare_copy_tasks(
                    source=source,
                    destination=destination,
                    entry_id=selected.id,
                    destination_parent=destination_parent_id,
                    state=state,
                    file_tasks=file_tasks,
                    manifest=manifest,
                    suggested_name=selected.name,
                )

            manifest["pending_tasks"] = [task.to_dict() for task in file_tasks]
            state.pending_files = len(file_tasks)
            _write_manifest(state.id, manifest)

            with ThreadPoolExecutor(max_workers=state.worker_count) as executor:
                futures = [
                    executor.submit(_copy_file_task, source, destination, state, task, manifest, state.id)
                    for task in file_tasks
                ]
                for future in as_completed(futures):
                    _ensure_not_cancelled(state)
                    future.result()

            _run_verification(
                destination=destination,
                state=state,
                manifest=manifest,
                manifest_job_id=state.id,
            )

            with state.lock:
                state.status = "completed"
                state.message = "Copy completed"
                state.current_item = ""
                state.resumable_job_id = None
            manifest["status"] = "completed"
            _write_manifest(state.id, manifest)
        except CopyCancelled:
            with state.lock:
                state.status = "cancelled"
                state.message = "Copy cancelled"
                state.current_item = ""
            manifest["status"] = "cancelled"
            _write_manifest(state.id, manifest)
        except Exception as ex:
            with state.lock:
                state.status = "failed"
                state.error = str(ex)
                state.message = "Copy failed"
                state.current_item = ""
            manifest["status"] = "failed"
            manifest["error"] = str(ex)
            _write_manifest(state.id, manifest)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return state


def _count_work_items(source: StorageProvider, selections: list[CopySelection]) -> int:
    total = 0
    for item in selections:
        if item.is_folder:
            total += 1 + _count_folder_children(source, item.id)
        else:
            total += 1
    return total


def _count_folder_children(source: StorageProvider, folder_id: str) -> int:
    count = 0
    for child in source.iter_entries(folder_id):
        count += 1
        if child.is_folder:
            count += _count_folder_children(source, child.id)
    return count


def _ensure_not_cancelled(state: JobState) -> None:
    if state.cancel_event.is_set():
        raise CopyCancelled()


def _prepare_copy_tasks(
    source: StorageProvider,
    destination: StorageProvider,
    entry_id: str,
    destination_parent: str | None,
    state: JobState,
    file_tasks: list[FileCopyTask],
    manifest: dict,
    suggested_name: str | None = None,
) -> None:
    _ensure_not_cancelled(state)
    entry = source.get_entry(entry_id)
    name = suggested_name or entry.name

    if entry.is_folder:
        with state.lock:
            state.current_item = name
        created = destination.create_folder(destination_parent, name)
        with state.lock:
            state.completed_items += 1
        manifest["copied_items"].append(
            {
                "name": name,
                "is_folder": True,
                "destination_id": created.id,
            }
        )
        manifest["verify_ids"].append(created.id)
        _write_manifest(state.id, manifest)
        for child in source.iter_entries(entry.id):
            _prepare_copy_tasks(
                source=source,
                destination=destination,
                entry_id=child.id,
                destination_parent=created.id,
                state=state,
                file_tasks=file_tasks,
                manifest=manifest,
                suggested_name=child.name,
            )
    else:
        file_tasks.append(
            FileCopyTask(
                entry_id=entry.id,
                destination_parent=destination_parent,
                name=name,
                size=entry.size,
            )
        )


def _copy_file_task(
    source: StorageProvider,
    destination: StorageProvider,
    state: JobState,
    task: FileCopyTask,
    manifest: dict,
    manifest_job_id: str,
) -> None:
    thread_name = threading.current_thread().name
    worker_key = _worker_key_for_thread(state, thread_name)
    _ensure_not_cancelled(state)
    with state.lock:
        state.current_item = task.name
        state.thread_activity[worker_key] = task.name

    is_large = (task.size or 0) >= LARGE_FILE_THRESHOLD_BYTES

    def _do_copy() -> None:
        file_content = source.download_file(task.entry_id)
        _ensure_not_cancelled(state)
        uploaded = destination.upload_file(task.destination_parent, task.name, file_content.data)
        with state.lock:
            state.copied_files += 1
            state.pending_files = max(0, state.pending_files - 1)
        with state.lock:
            pending = [t for t in manifest["pending_tasks"] if t.get("entry_id") != task.entry_id]
            manifest["pending_tasks"] = pending
            manifest["copied_items"].append(
                {
                    "name": task.name,
                    "is_folder": False,
                    "destination_id": uploaded.id,
                }
            )
            manifest["verify_ids"].append(uploaded.id)
        _write_manifest(manifest_job_id, manifest)

    try:
        if is_large:
            with state.large_file_lock:
                _ensure_not_cancelled(state)
                _do_copy()
        else:
            _do_copy()

        with state.lock:
            state.completed_items += 1
    finally:
        with state.lock:
            state.thread_activity[worker_key] = f"idle (last copied: {task.name})"


def _worker_key_for_thread(state: JobState, thread_name: str) -> str:
    with state.lock:
        existing = state.thread_slots.get(thread_name)
        if existing is not None:
            return f"Worker {existing}"

        used_slots = set(state.thread_slots.values())
        for slot in range(1, state.worker_count + 1):
            if slot not in used_slots:
                state.thread_slots[thread_name] = slot
                return f"Worker {slot}"

        fallback_slot = (len(state.thread_slots) % state.worker_count) + 1
        state.thread_slots[thread_name] = fallback_slot
        return f"Worker {fallback_slot}"


def _run_verification(
    destination: StorageProvider,
    state: JobState,
    manifest: dict,
    manifest_job_id: str,
) -> None:
    with state.lock:
        state.status = "verifying"
        state.message = "Verifying destination files"
        state.verify_status = "running"
        state.verify_total = len(manifest.get("verify_ids", []))
        state.verify_completed = 0

    manifest["verify_status"] = "running"
    _write_manifest(manifest_job_id, manifest)

    missing_ids: list[str] = []
    for destination_id in manifest.get("verify_ids", []):
        if state.verify_cancel_event.is_set():
            with state.lock:
                state.verify_status = "cancelled"
                state.verify_cancel_requested = True
                state.message = "Copy completed (verification skipped)"
                state.status = "completed"
                state.resumable_job_id = None
            manifest["verify_status"] = "cancelled"
            _write_manifest(manifest_job_id, manifest)
            return

        try:
            destination.get_entry(destination_id)
        except Exception:
            missing_ids.append(destination_id)

        with state.lock:
            state.verify_completed += 1

    with state.lock:
        state.verify_missing = len(missing_ids)
        state.verify_status = "completed"
        state.status = "running"
    manifest["verify_status"] = "completed"
    manifest["verify_missing"] = missing_ids
    _write_manifest(manifest_job_id, manifest)


def resume_copy_job(
    source: StorageProvider,
    destination: StorageProvider,
    job_id: str,
) -> JobState:
    manifest = _read_manifest(job_id)
    if manifest.get("status") not in {"failed", "cancelled", "running", "verifying"}:
        raise ProviderError("Only failed, cancelled, or interrupted jobs can be resumed")

    pending_payloads = manifest.get("pending_tasks", [])
    tasks = [FileCopyTask.from_dict(item) for item in pending_payloads]
    needs_verification_only = not tasks and (
        manifest.get("verify_status") in {"pending", "running", "cancelled"}
        or len(manifest.get("copied_items", [])) > 0
        or len(manifest.get("verify_missing", [])) > 0
    )
    if not tasks and not needs_verification_only:
        raise ProviderError("There are no pending files to resume")

    state = registry.create()
    state.worker_count = max(1, int(manifest.get("max_threads", 5)))
    state.thread_activity = {
        f"Worker {i}": "idle (resume pending)"
        for i in range(1, state.worker_count + 1)
    }
    state.total_items = len(tasks)
    state.pending_files = len(tasks)
    state.resumable_job_id = job_id
    state.manifest_path = str(_manifest_path(job_id))

    def run_resume() -> None:
        with state.lock:
            state.status = "running"
            state.message = "Resuming copy"

        try:
            if tasks:
                with ThreadPoolExecutor(max_workers=state.worker_count) as executor:
                    futures = [
                        executor.submit(_copy_file_task, source, destination, state, task, manifest, job_id)
                        for task in tasks
                    ]
                    for future in as_completed(futures):
                        _ensure_not_cancelled(state)
                        future.result()

            _run_verification(
                destination=destination,
                state=state,
                manifest=manifest,
                manifest_job_id=job_id,
            )

            with state.lock:
                state.status = "completed"
                state.message = "Copy completed"
                state.current_item = ""
                state.resumable_job_id = None
            manifest["status"] = "completed"
            _write_manifest(job_id, manifest)
        except CopyCancelled:
            with state.lock:
                state.status = "cancelled"
                state.message = "Copy cancelled"
                state.current_item = ""
            manifest["status"] = "cancelled"
            _write_manifest(job_id, manifest)
        except Exception as ex:
            with state.lock:
                state.status = "failed"
                state.error = str(ex)
                state.message = "Copy failed"
                state.current_item = ""
            manifest["status"] = "failed"
            manifest["error"] = str(ex)
            _write_manifest(job_id, manifest)

    thread = threading.Thread(target=run_resume, daemon=True)
    thread.start()
    return state


def request_verify_cancel(job_id: str) -> bool:
    job = registry.get(job_id)
    if not job:
        return False
    with job.lock:
        if job.status != "verifying":
            return False
        job.verify_cancel_requested = True
    job.verify_cancel_event.set()
    return True


def read_manifest_summary(job_id: str) -> dict:
    manifest = _read_manifest(job_id)
    return {
        "job_id": manifest.get("job_id", job_id),
        "status": manifest.get("status", "unknown"),
        "pending_files": len(manifest.get("pending_tasks", [])),
        "copied_files": len([x for x in manifest.get("copied_items", []) if not x.get("is_folder")]),
        "verify_status": manifest.get("verify_status", "pending"),
    }


def read_manifest_providers(job_id: str) -> tuple[str, str]:
    manifest = _read_manifest(job_id)
    return (
        manifest.get("source_provider", "local"),
        manifest.get("destination_provider", "local"),
    )
