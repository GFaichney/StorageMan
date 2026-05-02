# Cloud Storage Syncronisation Tool

## Overview

A GUI tool that allows the user to synchronise between cloud storage providers and their local machine

## Implementation Details

- Python app
- API and web frontend
- Runs on Windows, Mac and Linux
- All cloud file access is done via APIs
- startup scripts for each OS

## Supported Cloud Storage Providers

- Google Drive
= Dropbox

## Application Details

- Will start in the Synchronisation Screen
- Will have a link to the Config Dialog

## Application Sections

### Config Dialog

- Allows the user to enter API keys for storage providers:
  - Google Drive
  - Dropbox
- All config saved to disk
- All config loaded from disk when the application starts

### Synchronisation Screen

- Allows the user to select a source and destination
- Source and destination can be either a cloud storage provider or the local machine
- Allows the user to create folders on source and destination
- Allows the user to multi-select files and folders in the source
- Allows the user to copy all selected files and folders in the source to the destination
- Shows the user a progress dialog while files are being copied
