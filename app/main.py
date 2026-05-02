from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel, Field

from app.config_store import load_config, save_config
from app.models import CopySelection, ProviderType
from app.providers import (
    GOOGLE_AUTH_URI,
    GOOGLE_SCOPES,
    GOOGLE_TOKEN_URI,
    ProviderError,
    build_provider,
    extract_google_oauth_client_credentials,
    is_google_service_account_json,
)
from app.sync_jobs import registry, start_copy_job

app = FastAPI(title="StorageMan")

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
GOOGLE_OAUTH_SESSIONS: dict[str, dict] = {}


class ConfigPayload(BaseModel):
    google_drive_service_account_json: str = ""
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""
    google_drive_refresh_token: str = ""
    dropbox_access_token: str = ""
    max_transfer_threads: int = Field(default=5, ge=1, le=64)


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


class GoogleOAuthStartRequest(ConfigPayload):
    pass


def _build_google_oauth_client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": [redirect_uri],
        }
    }


def _cleanup_google_oauth_sessions() -> None:
    cutoff = time.time() - 900
    expired = [key for key, value in GOOGLE_OAUTH_SESSIONS.items() if value.get("created_at", 0) < cutoff]
    for key in expired:
        GOOGLE_OAUTH_SESSIONS.pop(key, None)


def _allow_insecure_google_oauth_for_localhost(base_url: str) -> None:
    # OAuthlib enforces HTTPS by default; localhost desktop flows commonly use HTTP callbacks.
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    config = load_config()
    return {
        "google_drive_service_account_json": config["google_drive"].get("service_account_json", ""),
        "google_drive_client_id": config["google_drive"].get("client_id", ""),
        "google_drive_client_secret": config["google_drive"].get("client_secret", ""),
        "google_drive_refresh_token": config["google_drive"].get("refresh_token", ""),
        "dropbox_access_token": config["dropbox"].get("access_token", ""),
        "max_transfer_threads": config.get("sync", {}).get("max_threads", 5),
    }


@app.post("/api/config")
def set_config(payload: ConfigPayload) -> dict:
    config = {
        "google_drive": {
            "service_account_json": payload.google_drive_service_account_json.strip(),
            "client_id": payload.google_drive_client_id.strip(),
            "client_secret": payload.google_drive_client_secret.strip(),
            "refresh_token": payload.google_drive_refresh_token.strip(),
        },
        "dropbox": {
            "access_token": payload.dropbox_access_token.strip(),
        },
        "sync": {
            "max_threads": payload.max_transfer_threads,
        },
    }
    save_config(config)
    return {"ok": True}


@app.post("/api/google/oauth/start")
def start_google_oauth(payload: GoogleOAuthStartRequest, request: Request) -> dict:
    _cleanup_google_oauth_sessions()
    _allow_insecure_google_oauth_for_localhost(str(request.base_url).rstrip("/"))

    credentials_json = payload.google_drive_service_account_json.strip()
    if credentials_json and is_google_service_account_json(credentials_json):
        raise HTTPException(
            status_code=400,
            detail="Service account JSON does not need OAuth. Paste OAuth client JSON here or enter a client ID and client secret.",
        )

    try:
        client_id, client_secret = extract_google_oauth_client_credentials(
            credentials_json,
            payload.google_drive_client_id,
            payload.google_drive_client_secret,
        )
    except ProviderError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth requires either OAuth client JSON or both client ID and client secret.",
        )

    redirect_uri = f"{str(request.base_url).rstrip('/')}/api/google/oauth/callback"
    state = secrets.token_urlsafe(24)
    flow = Flow.from_client_config(
        _build_google_oauth_client_config(client_id, client_secret, redirect_uri),
        scopes=GOOGLE_SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    GOOGLE_OAUTH_SESSIONS[state] = {
        "created_at": time.time(),
        "redirect_uri": redirect_uri,
        "payload": payload.model_dump(),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    return {"authorization_url": authorization_url}


@app.get("/api/google/oauth/callback")
def google_oauth_callback(request: Request, state: str | None = None, error: str | None = None) -> HTMLResponse:
    if error:
        return HTMLResponse(
            f"<html><body><h1>Google authorization failed</h1><p>{error}</p></body></html>",
            status_code=400,
        )

    if not state:
        return HTMLResponse(
            "<html><body><h1>Google authorization failed</h1><p>Missing OAuth state.</p></body></html>",
            status_code=400,
        )

    session = GOOGLE_OAUTH_SESSIONS.pop(state, None)
    if not session:
        return HTMLResponse(
            "<html><body><h1>Google authorization failed</h1><p>OAuth session expired or is invalid.</p></body></html>",
            status_code=400,
        )

    flow = Flow.from_client_config(
        _build_google_oauth_client_config(
            session["client_id"],
            session["client_secret"],
            session["redirect_uri"],
        ),
        scopes=GOOGLE_SCOPES,
        state=state,
    )
    flow.redirect_uri = session["redirect_uri"]

    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception as ex:
        return HTMLResponse(
            f"<html><body><h1>Google authorization failed</h1><p>{str(ex)}</p></body></html>",
            status_code=400,
        )

    refresh_token = flow.credentials.refresh_token
    if not refresh_token:
        return HTMLResponse(
            "<html><body><h1>Google authorization incomplete</h1><p>No refresh token was returned. Re-run the flow and ensure the consent screen is shown.</p></body></html>",
            status_code=400,
        )

    pending = session["payload"]
    config = {
        "google_drive": {
            "service_account_json": pending.get("google_drive_service_account_json", "").strip(),
            "client_id": session["client_id"],
            "client_secret": session["client_secret"],
            "refresh_token": refresh_token,
        },
        "dropbox": {
            "access_token": pending.get("dropbox_access_token", "").strip(),
        },
    }
    save_config(config)

    return HTMLResponse(
        """
        <html>
          <body style=\"font-family: sans-serif; padding: 24px;\">
            <h1>Google Drive connected</h1>
            <p>The refresh token has been saved to config.json. You can close this window and return to StorageMan.</p>
            <script>
              setTimeout(() => window.close(), 1200);
            </script>
          </body>
        </html>
        """
    )


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

    max_threads = config.get("sync", {}).get("max_threads", 5)
    try:
        max_threads = int(max_threads)
    except (TypeError, ValueError):
        max_threads = 5
    max_threads = max(1, min(max_threads, 64))

    state = start_copy_job(
        source=source,
        destination=destination,
        source_parent_id=payload.source_parent_id,
        destination_parent_id=payload.destination_parent_id,
        selections=selections,
        max_threads=max_threads,
    )
    return {"job_id": state.id}


@app.post("/api/copy/{job_id}/cancel")
def cancel_copy(job_id: str) -> dict:
    cancelled = registry.cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


@app.get("/api/copy/{job_id}")
def copy_status(job_id: str) -> dict:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    percentage = 0
    if job.total_items > 0:
        percentage = int((job.completed_items / job.total_items) * 100)

    thread_activity = [
        {"thread": thread_name, "item": item_name}
        for thread_name, item_name in sorted(job.thread_activity.items())
    ]

    return {
        "job_id": job.id,
        "status": job.status,
        "message": job.message,
        "error": job.error,
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "current_item": job.current_item,
        "cancel_requested": job.cancel_requested,
        "worker_count": job.worker_count,
        "thread_activity": thread_activity,
        "percentage": percentage,
    }


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
