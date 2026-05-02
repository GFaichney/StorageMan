from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from app.models import CopySelection, ProviderType
from app.providers import ProviderError, StorageProvider


@dataclass
class JobState:
    id: str
    status: str = "queued"
    message: str = ""
    total_items: int = 0
    completed_items: int = 0
    current_item: str = ""
    error: str = ""


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


registry = JobRegistry()


def start_copy_job(
    source: StorageProvider,
    destination: StorageProvider,
    source_parent_id: str | None,
    destination_parent_id: str | None,
    selections: list[CopySelection],
) -> JobState:
    state = registry.create()

    def run() -> None:
        state.status = "running"
        try:
            state.total_items = _count_work_items(source, selections)
            for selected in selections:
                state.current_item = selected.name
                _copy_entry(
                    source=source,
                    destination=destination,
                    entry_id=selected.id,
                    destination_parent=destination_parent_id,
                    state=state,
                    suggested_name=selected.name,
                )
            state.status = "completed"
            state.message = "Copy completed"
            state.current_item = ""
        except Exception as ex:
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


def _copy_entry(
    source: StorageProvider,
    destination: StorageProvider,
    entry_id: str,
    destination_parent: str | None,
    state: JobState,
    suggested_name: str | None = None,
) -> None:
    entry = source.get_entry(entry_id)
    name = suggested_name or entry.name

    if entry.is_folder:
        created = destination.create_folder(destination_parent, name)
        state.completed_items += 1
        for child in source.list_entries(entry.id):
            state.current_item = child.name
            _copy_entry(
                source=source,
                destination=destination,
                entry_id=child.id,
                destination_parent=created.id,
                state=state,
                suggested_name=child.name,
            )
    else:
        file_content = source.download_file(entry.id)
        destination.upload_file(destination_parent, name, file_content.data)
        state.completed_items += 1
