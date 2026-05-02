# StorageMan

Cloud storage synchronization tool with a Python API backend and a web frontend.

## Features

- Configure Google Drive and Dropbox API credentials
- Browse local files, Google Drive, and Dropbox with a unified model
- Create folders on local/cloud destinations
- Multi-select files/folders from a source and copy to a destination
- Progress-tracked copy jobs

## Quick Start

1. Create a Python virtual environment and install dependencies:
   - `pip install -r requirements.txt`
2. Start the app:
   - `python start.py`
3. Open your browser:
   - `http://127.0.0.1:8000`

`start.py` is cross-platform and works on Windows, Linux, and macOS. It will use `.venv` automatically when available, install dependencies, and start the app.

## OS Startup Scripts

- Windows: `start_windows.bat`
- Linux: `start_linux.sh`
- macOS: `start_macos.sh`

These scripts install dependencies and start the app. For Linux/macOS, make the script executable first:

- `chmod +x start_linux.sh start_macos.sh`

## Notes

- Google Drive uses OAuth desktop credentials (`client_id`, `client_secret`).
- Dropbox uses an access token.
- Configuration is stored in `config.json` in the project root.
