from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import uuid
from dataclasses import dataclass, field

from app.models import CopySelection
from app.providers import StorageProvider


class CopyCancelled(RuntimeError):
    pass


@dataclass
class FileCopyTask:
    entry_id: str
    destination_parent: str | None
    name: str
    size: int | None = None


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
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    large_file_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


LARGE_FILE_THRESHOLD_BYTES = 64 * 1024 * 1024


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

    def run() -> None:
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
                    suggested_name=selected.name,
                )

            with ThreadPoolExecutor(max_workers=state.worker_count) as executor:
                futures = [
                    executor.submit(_copy_file_task, source, destination, state, task)
                    for task in file_tasks
                ]
                for future in as_completed(futures):
                    _ensure_not_cancelled(state)
                    future.result()

            with state.lock:
                state.status = "completed"
                state.message = "Copy completed"
                state.current_item = ""
        except CopyCancelled:
            with state.lock:
                state.status = "cancelled"
                state.message = "Copy cancelled"
                state.current_item = ""
        except Exception as ex:
            with state.lock:
                state.status = "failed"
                state.error = str(ex)
                state.message = "Copy failed"
                state.current_item = ""

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
    for child in source.list_entries(folder_id):
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
        for child in source.list_entries(entry.id):
            _prepare_copy_tasks(
                source=source,
                destination=destination,
                entry_id=child.id,
                destination_parent=created.id,
                state=state,
                file_tasks=file_tasks,
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
        destination.upload_file(task.destination_parent, task.name, file_content.data)

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
