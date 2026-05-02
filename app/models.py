from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderType = Literal["local", "gdrive", "dropbox"]


@dataclass
class Entry:
    id: str
    name: str
    is_folder: bool
    size: int | None = None


@dataclass
class FileContent:
    name: str
    data: bytes


@dataclass
class CopySelection:
    id: str
    name: str
    is_folder: bool
