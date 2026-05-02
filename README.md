# StorageMan

Cloud storage synchronization tool with a Python API backend and a web frontend.

## Features

- Configure Google Drive and Dropbox API credentials
- Configure the maximum number of concurrent transfer threads
- Browse local files, Google Drive, and Dropbox with a unified model
- Create folders on local/cloud destinations
- Multi-select files/folders from a source and copy to a destination
- Progress-tracked copy jobs with cancellation
- Per-thread activity details in the progress dialog when concurrent copy threads are enabled

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

- Google Drive does not support browsing user files with a single API key.
- For single-credential Google Drive access, use a service account JSON key in the config dialog and share the target Drive folders with that service account email.
- OAuth desktop credentials are also supported. You can either enter `client_id`, `client_secret`, and `refresh_token` manually, or paste the Google OAuth client JSON and provide only the `refresh_token` separately.
- The config dialog now includes a built-in `Generate Google Refresh Token` flow. Paste OAuth client JSON or enter client ID and secret, run the flow, complete Google consent, and the app will save the refresh token automatically.
- File copy operations run concurrently using a configurable max thread count (default: 5).
- Dropbox uses an access token.
- Configuration is stored in `config.json` in the project root.
