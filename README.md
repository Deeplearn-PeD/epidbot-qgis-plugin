# EpidBot QGIS Plugin

[![Tests](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Deeplearn-PeD/epidbot-qgis-plugin)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/releases/latest)
[![Download ZIP](https://img.shields.io/badge/download-epidbot.zip-2ea44f?logo=github)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/releases/latest/download/epidbot.zip)
[![License: GPL v2+](https://img.shields.io/github/license/Deeplearn-PeD/epidbot-qgis-plugin)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![QGIS](https://img.shields.io/badge/QGIS-3.28%2B-589632?logo=qgis&logoColor=white)](https://qgis.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Issues](https://img.shields.io/github/issues/Deeplearn-PeD/epidbot-qgis-plugin)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/issues)

Chat with **EpidBot** — the AI assistant for Brazilian public health data
(DATASUS/SINAN, malaria Atlas, and more) — directly inside QGIS.

This is a standalone repository; the plugin talks to an EpidBot server over
its REST API (`/api/v1/...`) and does not import the `epidbot` Python
package.

## Features

- **Dockable chat panel** — ask questions about epidemiological data without
  leaving QGIS. Answers render with basic Markdown, including inline chart
  thumbnails.
- **Map-context injection** — a toggle (on by default) prepends your active
  layers, their CRS and feature counts, the reprojected canvas extent
  (EPSG:4326), and the project CRS to every prompt, so EpidBot can reason
  about *what is on your map*.
- **Two authentication modes** — EpidBot username/password (JWT with
  automatic refresh) or a personal API key (`X-API-Key`). Secrets go to the
  system keyring when the optional `keyring` package is available, with a
  documented QSettings fallback.
- **Sessions** — pick an existing chat session, start a new one, or let the
  server auto-create one per conversation. History (including plots) is
  reloaded when switching sessions.
- **Plot actions** — every agent-generated chart can be saved to disk,
  opened in the image viewer, or embedded into an *"EpidBot Plots"* print
  layout.
- **Non-blocking UI** — chat submission/polling runs on a worker thread;
  the Submit + Poll REST pattern (`POST /chat` → `GET /chat/{job_id}`) keeps
  the plugin simple and proxy-friendly (no WebSocket dependency).
- **Internationalized** — English and Brazilian Portuguese UI; the answer
  language sent to EpidBot is configurable.

## Installation (from ZIP)

1. **Download the latest plugin ZIP**:
   **[epidbot.zip](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/releases/latest/download/epidbot.zip)**
   — always points at the most recent release. (To build it yourself, run
   `./scripts/package.sh` → `build/epidbot-<version>.zip`.)
2. In QGIS: *Plugins → Manage and Install Plugins → Install from ZIP* and
   select the downloaded file.
3. Open the *EpidBot Chat* panel from the toolbar and configure the server
   URL and credentials via the ⚙ button in the panel.

## Configuration

| Setting | Meaning |
|---|---|
| Server URL | Base URL of the EpidBot instance, e.g. `https://kwar.example` |
| Authentication | `EpidBot account (password)` or `API key` |
| Answer language | `en` / `pt-BR` (sent as `locale` to `/chat`) |
| Status poll interval | Seconds between job-status polls (default 1.5 s) |

Settings live in QSettings (`EpidBot` / `EpidBotQGISPlugin`); tokens and the
API key are stored in the system keyring when `keyring` is installed.

## API endpoints used

- `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `GET /api/v1/auth/me`
- `POST /api/v1/chat`, `GET /api/v1/chat/{job_id}`
- `GET/POST /api/v1/sessions`, `GET /api/v1/sessions/{id}/messages`
- `GET /api/v1/sessions/plots/{filename}`

## Development

Requires Python ≥ 3.9 with `pytest` (QGIS is **not** needed for the core
test suite — UI modules need a QGIS/PyQt5 runtime and are compile-checked
only):

```bash
pytest tests/ -v                      # core logic tests (no QGIS needed)
python -m py_compile plugin.py core/chat_worker.py ui/*.py   # syntax check
./scripts/package.sh                  # build installable ZIP
```

Repository layout:

```
metadata.txt          QGIS plugin manifest
plugin.py             Plugin controller (actions, dock, i18n)
core/                 Pure-Python logic (API client, auth store,
                      map context, markdown) - QGIS-independent, tested
core/chat_worker.py   QThread glue (Qt required)
ui/                   Qt widgets (chat dock, settings, message bubbles)
i18n/                 Qt translation sources (en, pt-BR)
icons/                Plugin icon
tests/                Unit tests for the core modules
```

## Roadmap

- **M3**: dataset catalog browser (`GET /api/v1/catalog/datasets` → export →
  load as `QgsVectorLayer`), document upload to sessions
  (`POST /sessions/{id}/documents`), submission to the official QGIS plugin
  repository.

## License

GPL-2.0-or-later (see `LICENSE`).
