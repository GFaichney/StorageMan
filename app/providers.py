from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Iterable

import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata, FolderMetadata
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from app.models import Entry, FileContent, ProviderType

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/drive"]


class ProviderError(RuntimeError):
    pass


class StorageProvider:
    provider_type: ProviderType

    def list_entries(self, parent_id: str | None) -> list[Entry]:
        raise NotImplementedError

    def create_folder(self, parent_id: str | None, name: str) -> Entry:
        raise NotImplementedError

    def download_file(self, entry_id: str) -> FileContent:
        raise NotImplementedError

    def upload_file(self, parent_id: str | None, name: str, data: bytes) -> Entry:
        raise NotImplementedError

    def get_entry(self, entry_id: str) -> Entry:
        raise NotImplementedError


class LocalProvider(StorageProvider):
    provider_type: ProviderType = "local"

    def __init__(self) -> None:
        self.root = Path.home().resolve()

    def _resolve_parent(self, parent_id: str | None) -> Path:
        if not parent_id:
            return self.root
        p = Path(parent_id).resolve()
        if not p.exists() or not p.is_dir():
            raise ProviderError(f"Local folder not found: {p}")
        return p

    def list_entries(self, parent_id: str | None) -> list[Entry]:
        parent = self._resolve_parent(parent_id)
        entries: list[Entry] = []
        try:
            for child in parent.iterdir():
                is_folder = child.is_dir()
                size = None if is_folder else child.stat().st_size
                entries.append(
                    Entry(
                        id=str(child.resolve()),
                        name=child.name,
                        is_folder=is_folder,
                        size=size,
                    )
                )
        except PermissionError as ex:
            raise ProviderError(f"Permission denied: {parent}") from ex
        return sorted(entries, key=lambda e: (not e.is_folder, e.name.lower()))

    def create_folder(self, parent_id: str | None, name: str) -> Entry:
        parent = self._resolve_parent(parent_id)
        folder_path = (parent / name).resolve()
        folder_path.mkdir(parents=False, exist_ok=False)
        return Entry(id=str(folder_path), name=folder_path.name, is_folder=True)

    def download_file(self, entry_id: str) -> FileContent:
        path = Path(entry_id).resolve()
        if not path.is_file():
            raise ProviderError("Local file not found")
        return FileContent(name=path.name, data=path.read_bytes())

    def upload_file(self, parent_id: str | None, name: str, data: bytes) -> Entry:
        parent = self._resolve_parent(parent_id)
        target = (parent / name).resolve()
        target.write_bytes(data)
        return Entry(id=str(target), name=target.name, is_folder=False, size=len(data))

    def get_entry(self, entry_id: str) -> Entry:
        path = Path(entry_id).resolve()
        if not path.exists():
            raise ProviderError("Path not found")
        is_folder = path.is_dir()
        return Entry(
            id=str(path),
            name=path.name,
            is_folder=is_folder,
            size=None if is_folder else path.stat().st_size,
        )


class DropboxProvider(StorageProvider):
    provider_type: ProviderType = "dropbox"

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ProviderError("Dropbox access token is missing")
        self.client = dropbox.Dropbox(access_token)

    def _normalize_path(self, parent_id: str | None) -> str:
        if not parent_id or parent_id == "/":
            return ""
        if not parent_id.startswith("/"):
            return f"/{parent_id}"
        return parent_id

    def _join(self, parent: str, name: str) -> str:
        if not parent:
            return f"/{name}"
        return f"{parent.rstrip('/')}/{name}"

    def list_entries(self, parent_id: str | None) -> list[Entry]:
        folder = self._normalize_path(parent_id)
        try:
            result = self.client.files_list_folder(path=folder)
        except ApiError as ex:
            raise ProviderError(f"Failed to list Dropbox folder: {ex}") from ex

        items: list[Entry] = []
        for item in result.entries:
            if isinstance(item, FolderMetadata):
                items.append(Entry(id=item.path_lower or item.path_display or "", name=item.name, is_folder=True))
            elif isinstance(item, FileMetadata):
                items.append(
                    Entry(
                        id=item.path_lower or item.path_display or "",
                        name=item.name,
                        is_folder=False,
                        size=item.size,
                    )
                )
        return sorted(items, key=lambda e: (not e.is_folder, e.name.lower()))

    def create_folder(self, parent_id: str | None, name: str) -> Entry:
        parent = self._normalize_path(parent_id)
        path = self._join(parent, name)
        try:
            created = self.client.files_create_folder_v2(path=path)
        except ApiError as ex:
            raise ProviderError(f"Failed to create Dropbox folder: {ex}") from ex
        metadata = created.metadata
        return Entry(id=metadata.path_lower or metadata.path_display or path, name=metadata.name, is_folder=True)

    def download_file(self, entry_id: str) -> FileContent:
        path = self._normalize_path(entry_id)
        try:
            metadata, response = self.client.files_download(path=path)
        except ApiError as ex:
            raise ProviderError(f"Failed to download Dropbox file: {ex}") from ex
        return FileContent(name=metadata.name, data=response.content)

    def upload_file(self, parent_id: str | None, name: str, data: bytes) -> Entry:
        parent = self._normalize_path(parent_id)
        path = self._join(parent, name)
        try:
            metadata = self.client.files_upload(data, path, mode=dropbox.files.WriteMode.overwrite)
        except ApiError as ex:
            raise ProviderError(f"Failed to upload Dropbox file: {ex}") from ex
        return Entry(id=metadata.path_lower or metadata.path_display or path, name=metadata.name, is_folder=False, size=metadata.size)

    def get_entry(self, entry_id: str) -> Entry:
        path = self._normalize_path(entry_id)
        try:
            md = self.client.files_get_metadata(path)
        except ApiError as ex:
            raise ProviderError(f"Failed to fetch Dropbox metadata: {ex}") from ex
        if isinstance(md, FolderMetadata):
            return Entry(id=md.path_lower or md.path_display or path, name=md.name, is_folder=True)
        if isinstance(md, FileMetadata):
            return Entry(id=md.path_lower or md.path_display or path, name=md.name, is_folder=False, size=md.size)
        raise ProviderError("Unsupported Dropbox metadata type")


class GoogleDriveProvider(StorageProvider):
    provider_type: ProviderType = "gdrive"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        if not client_id or not client_secret or not refresh_token:
            raise ProviderError("Google Drive credentials are incomplete")

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=GOOGLE_SCOPES,
        )
        creds.refresh(Request())
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _parent(self, parent_id: str | None) -> str:
        return parent_id or "root"

    def list_entries(self, parent_id: str | None) -> list[Entry]:
        parent = self._parent(parent_id)
        query = f"'{parent}' in parents and trashed=false"
        try:
            result = (
                self.service.files()
                .list(
                    q=query,
                    fields="files(id,name,mimeType,size)",
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except HttpError as ex:
            raise ProviderError(f"Failed to list Google Drive folder: {ex}") from ex

        entries: list[Entry] = []
        for f in result.get("files", []):
            is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
            size = None
            if not is_folder and "size" in f:
                try:
                    size = int(f["size"])
                except (TypeError, ValueError):
                    size = None
            entries.append(Entry(id=f["id"], name=f["name"], is_folder=is_folder, size=size))
        return sorted(entries, key=lambda e: (not e.is_folder, e.name.lower()))

    def create_folder(self, parent_id: str | None, name: str) -> Entry:
        parent = self._parent(parent_id)
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent],
        }
        try:
            created = (
                self.service.files()
                .create(body=body, fields="id,name,mimeType", supportsAllDrives=True)
                .execute()
            )
        except HttpError as ex:
            raise ProviderError(f"Failed to create Google Drive folder: {ex}") from ex
        return Entry(id=created["id"], name=created["name"], is_folder=True)

    def download_file(self, entry_id: str) -> FileContent:
        meta = self.get_entry(entry_id)
        request = self.service.files().get_media(fileId=entry_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        try:
            while not done:
                _, done = downloader.next_chunk()
        except HttpError as ex:
            raise ProviderError(f"Failed to download Google Drive file: {ex}") from ex
        return FileContent(name=meta.name, data=fh.getvalue())

    def upload_file(self, parent_id: str | None, name: str, data: bytes) -> Entry:
        parent = self._parent(parent_id)
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/octet-stream")
        body = {
            "name": name,
            "parents": [parent],
        }
        try:
            created = (
                self.service.files()
                .create(body=body, media_body=media, fields="id,name,size", supportsAllDrives=True)
                .execute()
            )
        except HttpError as ex:
            raise ProviderError(f"Failed to upload Google Drive file: {ex}") from ex
        size = int(created.get("size", len(data))) if created.get("size") else len(data)
        return Entry(id=created["id"], name=created["name"], is_folder=False, size=size)

    def get_entry(self, entry_id: str) -> Entry:
        try:
            metadata = (
                self.service.files()
                .get(fileId=entry_id, fields="id,name,mimeType,size", supportsAllDrives=True)
                .execute()
            )
        except HttpError as ex:
            raise ProviderError(f"Failed to read Google Drive metadata: {ex}") from ex

        is_folder = metadata.get("mimeType") == "application/vnd.google-apps.folder"
        size = None if is_folder else int(metadata.get("size", 0))
        return Entry(id=metadata["id"], name=metadata["name"], is_folder=is_folder, size=size)


def build_provider(provider: ProviderType, config: dict) -> StorageProvider:
    if provider == "local":
        return LocalProvider()
    if provider == "dropbox":
        return DropboxProvider(config.get("dropbox", {}).get("access_token", ""))
    if provider == "gdrive":
        gd = config.get("google_drive", {})
        return GoogleDriveProvider(
            client_id=gd.get("client_id", ""),
            client_secret=gd.get("client_secret", ""),
            refresh_token=gd.get("refresh_token", ""),
        )
    raise ProviderError(f"Unsupported provider: {provider}")
