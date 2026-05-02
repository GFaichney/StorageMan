from __future__ import annotations

from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config_store import load_config, save_config
from app.models import CopySelection, ProviderType
from app.providers import ProviderError, build_provider
from app.sync_jobs import registry, start_copy_job

app = FastAPI(title="StorageMan")

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ConfigPayload(BaseModel):
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""
    google_drive_refresh_token: str = ""
    dropbox_access_token: str = ""


class ListResponseEntry(BaseModel):
    id: str
    name: str
    is_folder: bool
    size: int | None = None


class CreateFolderRequest(BaseModel):
    provider: ProviderType
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=255)


class CopyItem(BaseModel):
    id: str
    name: str
    is_folder: bool


class StartCopyRequest(BaseModel):
    source_provider: ProviderType
    destination_provider: ProviderType
    source_parent_id: str | None = None
    destination_parent_id: str | None = None
    selections: list[CopyItem]


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    config = load_config()
    return {
        "google_drive_client_id": config["google_drive"].get("client_id", ""),
        "google_drive_client_secret": config["google_drive"].get("client_secret", ""),
        "google_drive_refresh_token": config["google_drive"].get("refresh_token", ""),
        "dropbox_access_token": config["dropbox"].get("access_token", ""),
    }


@app.post("/api/config")
def set_config(payload: ConfigPayload) -> dict:
    config = {
        "google_drive": {
            "client_id": payload.google_drive_client_id.strip(),
            "client_secret": payload.google_drive_client_secret.strip(),
            "refresh_token": payload.google_drive_refresh_token.strip(),
        },
        "dropbox": {
            "access_token": payload.dropbox_access_token.strip(),
        },
    }
    save_config(config)
    return {"ok": True}


@app.get("/api/list")
def list_entries(provider: ProviderType, parent_id: str | None = None) -> dict:
    config = load_config()
    try:
        client = build_provider(provider, config)
        entries = client.list_entries(parent_id)
    except ProviderError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex

    return {
        "entries": [
            {
                "id": e.id,
                "name": e.name,
                "is_folder": e.is_folder,
                "size": e.size,
            }
            for e in entries
        ]
    }


@app.post("/api/folder")
def create_folder(payload: CreateFolderRequest) -> dict:
    config = load_config()
    try:
        client = build_provider(payload.provider, config)
        created = client.create_folder(payload.parent_id, payload.name.strip())
    except ProviderError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Folder already exists")

    return {
        "id": created.id,
        "name": created.name,
        "is_folder": created.is_folder,
    }


@app.post("/api/copy/start")
def start_copy(payload: StartCopyRequest) -> dict:
    if not payload.selections:
        raise HTTPException(status_code=400, detail="No items selected")

    config = load_config()
    try:
        source = build_provider(payload.source_provider, config)
        destination = build_provider(payload.destination_provider, config)
    except ProviderError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex

    selections = [
        CopySelection(id=item.id, name=item.name, is_folder=item.is_folder)
        for item in payload.selections
    ]

    state = start_copy_job(
        source=source,
        destination=destination,
        source_parent_id=payload.source_parent_id,
        destination_parent_id=payload.destination_parent_id,
        selections=selections,
    )
    return {"job_id": state.id}


@app.get("/api/copy/{job_id}")
def copy_status(job_id: str) -> dict:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    percentage = 0
    if job.total_items > 0:
        percentage = int((job.completed_items / job.total_items) * 100)

    return {
        "job_id": job.id,
        "status": job.status,
        "message": job.message,
        "error": job.error,
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "current_item": job.current_item,
        "percentage": percentage,
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
