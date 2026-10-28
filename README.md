# EpidBot QGIS Plugin

[![Tests](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Deeplearn-PeD/epidbot-qgis-plugin)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/releases/latest)
[![Download ZIP](https://img.shields.io/badge/download-epidbot.zip-2ea44f?logo=github)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/releases/latest/download/epidbot.zip)
[![License: GPL v2+](https://img.shields.io/github/license/Deeplearn-PeD/epidbot-qgis-plugin)](LICENSE)
[![QGIS plugin repository](https://img.shields.io/badge/QGIS%20plugins-epidbot-589632?logo=qgis&logoColor=white)](https://plugins.qgis.org/plugins/epidbot/)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![QGIS](https://img.shields.io/badge/QGIS-3.28%2B-589632?logo=qgis&logoColor=white)](https://qgis.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Issues](https://img.shields.io/github/issues/Deeplearn-PeD/epidbot-qgis-plugin)](https://github.com/Deeplearn-PeD/epidbot-qgis-plugin/issues)

Chat with **EpidBot** — the AI assistant for public health and epidemiological
data, from Brazilian DATASUS systems (SINAN, SIM, SIA, SIH) to WHO, PAHO,
ECDC, CDC and the Malaria Atlas — directly inside QGIS.

This is a standalone repository; the plugin talks to an EpidBot server over
its REST API (`/api/v1/...`) and does not import the `epidbot` Python
package.

## Features

- **Dockable chat panel** — ask questions about epidemiological data without
  leaving QGIS. Answers render with basic Markdown, including inline chart
  thumbnails.
- **Map integration** — EpidBot understands your map and can feed it back:
  - *Map-context injection* — active layers (with fields, sample values and
    selections), canvas extent (EPSG:4326) and project CRS are prepended to
    every prompt.
  - *Spatial prompts* — one-click toolbar actions to ask about the current
    map extent or the features you have selected.
  - *"Add to map"* — GeoJSON blocks, dataset URLs and EpidBot artifacts in
    answers become styled map layers on the canvas with a single click.
- **Layer exchange** — send layers *to* EpidBot: attach selected features,
  the extent snapshot, or a whole layer to any message (with a size budget
  and interactive reduction options). Larger layers can be uploaded as
  server artifacts when your EpidBot server supports them.
- **Attribute write-back** — ask EpidBot to enrich your layers ("add a
  field with population counts"): proposed edits arrive as reviewable
  `epidbot-edit` operations and are only applied after you inspect a
  preview (matched features, unmatched keys, value samples) — inside a
  normal QGIS edit session, so undo always works.
- **API-key authentication** — a personal EpidBot API key is sent on every
  request via the `X-API-Key` header. The key is stored in the system
  keyring when the optional `keyring` package is available, with a
  documented QSettings fallback.
- **Sessions** — pick an existing chat session, start a new one, or let the
  server auto-create one per conversation. History (including plots) is
  reloaded when switching sessions. Conversations can be exported to
  Markdown.
- **Plot actions** — every agent-generated chart can be saved to disk,
  opened in the image viewer, or embedded into an *"EpidBot Plots"* print
  layout.
- **Non-blocking UI** — chat submission/polling runs on a worker thread;
  the Submit + Poll REST pattern (`POST /chat` → `GET /chat/{job_id}`) keeps
  the plugin simple and proxy-friendly (no WebSocket dependency). Transient
  network failures are retried automatically.
- **Internationalized** — English and Brazilian Portuguese UI; the answer
  language sent to EpidBot is configurable.

## Installation

To use the plugin you need an EpidBot account on a paid plan (**Pro, Max or Team**)
and a personal API key created under *Settings → Integrations → API Keys*.

### From the official QGIS plugin repository (recommended)

The plugin is published as [EpidBot](https://plugins.qgis.org/plugins/epidbot/)
(plugin ID **6764**) in the QGIS plugin repository:

1. In QGIS: *Plugins → Manage and Install Plugins*
2. Search for **EpidBot** and click *Install Plugin*.
3. Open the *EpidBot Chat* panel from the toolbar and configure the server
   URL and credentials via the ⚙ button in the panel.

Installed this way the plugin is updated by the QGIS plugin manager itself.

### From ZIP (newer than the published version)

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
| Server URL | EpidBot API base URL: `https://api.epidbot.kwar-ai.com.br` (pre-filled default) |
| API key | Personal EpidBot API key (created in the EpidBot web UI), sent as `X-API-Key` on every request |
| Answer language | `en` / `pt-BR` (sent as `locale` to `/chat`) |
| Status poll interval | Seconds between job-status polls (default 1.5 s) |

Settings live in QSettings (`EpidBot` / `EpidBotQGISPlugin`); the API key is
stored in the system keyring when `keyring` is installed.

## API endpoints used

- `GET /api/v1/auth/me`
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
